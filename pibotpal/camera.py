# SPDX-License-Identifier: MIT
"""CSI camera capture using the system LGPL libcamera bindings directly.

One dispatcher drains the process-wide camera manager and routes completions to
individual cameras. Independent capture threads must never drain that shared
completion queue themselves: doing so loses the other camera's frame buffers.
"""
import hashlib
import itertools
import mmap
import math
import queue
import selectors
import threading
import time


def capture_metadata(values):
    """Copy selected scalar diagnostics before a completed request is reused."""
    allowed = {'ExposureTime', 'AnalogueGain', 'DigitalGain', 'SensorTimestamp', 'Lux'}
    result = {}
    for control, value in values.items():
        name = control if isinstance(control, str) else getattr(control, 'name', None)
        if name in allowed and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[name] = value
    return result


def camera_identifier(hardware_id):
    return 'csi-' + hashlib.sha256(hardware_id.encode()).hexdigest()[:12]


class RequestRouter:
    """Route each request cookie to its owner; no frame memory is copied here."""
    def __init__(self, manager):
        self.manager = manager
        self._lock = threading.Lock()
        self._owners = {}
        self._cookies = itertools.count(1)
        self._thread = None
        self._error = None

    def register(self, destination):
        with self._lock:
            cookie = next(self._cookies)
            self._owners[cookie] = destination
            return cookie

    def unregister(self, cookie):
        with self._lock:
            self._owners.pop(cookie, None)

    def dispatch(self, requests):
        with self._lock:
            for request in requests:
                destination = self._owners.get(request.cookie)
                if destination is not None:
                    # Four buffers per camera bound the number of outstanding
                    # completions. A request is requeued only after consumption.
                    destination.put(request)

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._error = None
            self._thread = threading.Thread(target=self._run, name='camera-completions', daemon=True)
            self._thread.start()

    def _run(self):
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.manager.event_fd, selectors.EVENT_READ)
                while True:
                    if selector.select(.25):
                        self.dispatch(self.manager.get_ready_requests())
        except Exception as exc:
            self._error = str(exc)


_router = None
_router_lock = threading.Lock()


def get_router():
    global _router
    with _router_lock:
        if _router is None:
            import libcamera
            _router = RequestRouter(libcamera.CameraManager.singleton())
        return _router


def list_cameras():
    """Enumerate CSI sensors without acquiring or starting them."""
    import libcamera
    result = []
    for index, device in enumerate(get_router().manager.cameras):
        properties = device.properties
        result.append({'id': camera_identifier(device.id), 'hardware_id': device.id,
                       'index': index, 'model': str(properties.get(libcamera.properties.Model, 'CSI camera')),
                       'sensor_rotation': int(properties.get(libcamera.properties.Rotation, 0)),
                       'connection': 'csi'})
    return result


class Camera:
    def __init__(self, camera_id=None):
        import libcamera
        self.api = libcamera
        self.router = get_router()
        self.manager = self.router.manager
        devices = self.manager.cameras
        if not devices:
            raise RuntimeError('No CSI camera detected')
        self.device = next((device for device in devices if camera_id in (device.id, camera_identifier(device.id))), None) if camera_id else devices[0]
        if self.device is None:
            raise ValueError('Unknown CSI camera')
        self.device.acquire()
        self.running = False
        self.last_metadata = {}
        self.closed = False
        self.maps = []
        self.requests = []
        self.images = {}
        self._ready = queue.SimpleQueue()

    def create_video_configuration(self, main, controls=None):
        return {'main': main, 'controls': controls or {}}

    def configure(self, options):
        cfg = self.device.generate_configuration([self.api.StreamRole.Viewfinder])
        stream = cfg.at(0)
        width, height = options['main']['size']
        stream.size = self.api.Size(width, height)
        stream.pixel_format = self.api.PixelFormat('RGB888')
        stream.buffer_count = 4
        cfg.validate()
        if str(stream.pixel_format) != 'RGB888' or (stream.size.width, stream.size.height) != (width, height):
            raise RuntimeError('Camera cannot supply the requested RGB stream')
        self.device.configure(cfg)
        self.configuration = cfg
        self.stream = stream.stream
        self.width, self.height, self.stride = width, height, stream.stride
        self.allocator = self.api.FrameBufferAllocator(self.device)
        self.allocator.allocate(self.stream)
        self.buffers = self.allocator.buffers(self.stream)
        rate = options['controls'].get('FrameRate', 12)
        for buffer in self.buffers:
            if len(buffer.planes) != 1:
                raise RuntimeError('Expected one RGB memory plane')
            plane = buffer.planes[0]
            offset = plane.offset - plane.offset % mmap.PAGESIZE
            mapping = mmap.mmap(plane.fd, plane.length + plane.offset - offset,
                                flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=offset)
            self.maps.append(mapping)
            cookie = self.router.register(self._ready)
            try:
                request = self.device.create_request(cookie)
                request.add_buffer(self.stream, buffer)
                request.set_control(self.api.controls.FrameDurationLimits, (int(1e6/rate), int(1e6/rate)))
            except Exception:
                self.router.unregister(cookie)
                raise
            self.images[cookie] = (mapping, plane.offset-offset)
            self.requests.append(request)

    def start(self):
        self.router.start()
        self.device.start()
        self.running = True
        for request in self.requests:
            self.device.queue_request(request)

    def capture_array(self, name='main'):
        import numpy as np
        deadline = time.monotonic() + 3
        while self.running and time.monotonic() < deadline:
            if self.router._error:
                raise RuntimeError('Camera dispatcher failed: ' + self.router._error)
            try:
                request = self._ready.get(timeout=.25)
            except queue.Empty:
                continue
            if request.status != self.api.Request.Status.Complete:
                continue
            mapping, offset = self.images[request.cookie]
            image = np.ndarray((self.height, self.width, 3), dtype=np.uint8,
                               buffer=mapping, offset=offset, strides=(self.stride, 3, 1)).copy()
            self.last_metadata = capture_metadata(request.metadata)
            request.reuse()
            self.device.queue_request(request)
            return image
        raise RuntimeError('Camera capture timed out or stopped')

    def stop(self):
        if self.running:
            self.running = False
            self.device.stop()

    def close(self):
        if self.closed:
            return
        self.stop()
        for cookie in self.images:
            self.router.unregister(cookie)
        self.requests.clear()
        while not self._ready.empty():
            self._ready.get_nowait()
        for mapping in self.maps:
            mapping.close()
        self.maps.clear()
        self.images.clear()
        if hasattr(self, 'allocator'):
            self.buffers = []
            del self.allocator
        self.device.release()
        self.closed = True

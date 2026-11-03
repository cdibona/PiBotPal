# SPDX-License-Identifier: MIT
"""Single RGB stream using the system libcamera bindings directly.

No Picamera2, FFmpeg, OpenCV or process-control extensions are imported.
The system libcamera library retains its LGPL license.
"""
import mmap
import selectors
import time


class Camera:
    def __init__(self):
        import libcamera
        self.api = libcamera
        self.manager = libcamera.CameraManager.singleton()
        if not self.manager.cameras:
            raise RuntimeError('No camera detected')
        self.device = self.manager.cameras[0]
        self.device.acquire()
        self.running = False
        self.maps = []
        self.requests = []
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.manager.event_fd, selectors.EVENT_READ)

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
        self.images = {}
        rate = options['controls'].get('FrameRate', 12)
        for buffer in self.buffers:
            if len(buffer.planes) != 1:
                raise RuntimeError('Expected one RGB memory plane')
            plane = buffer.planes[0]
            offset = plane.offset - plane.offset % mmap.PAGESIZE
            mapping = mmap.mmap(plane.fd, plane.length + plane.offset - offset,
                                flags=mmap.MAP_SHARED, prot=mmap.PROT_READ, offset=offset)
            self.maps.append(mapping)
            request = self.device.create_request(len(self.requests))
            request.add_buffer(self.stream, buffer)
            request.set_control(self.api.controls.FrameDurationLimits, (int(1e6/rate), int(1e6/rate)))
            self.images[request.cookie] = (mapping, plane.offset-offset)
            self.requests.append(request)

    def start(self):
        self.device.start()
        self.running = True
        for request in self.requests:
            self.device.queue_request(request)

    def capture_array(self, name='main'):
        import numpy as np
        deadline = time.monotonic() + 3
        while self.running and time.monotonic() < deadline:
            if not self.selector.select(.25):
                continue
            ready = self.manager.get_ready_requests()
            image = None
            for request in ready:
                if request.status == self.api.Request.Status.Complete:
                    mapping, offset = self.images[request.cookie]
                    image = np.ndarray((self.height, self.width, 3), dtype=np.uint8,
                                       buffer=mapping, offset=offset,
                                       strides=(self.stride, 3, 1)).copy()
                    request.reuse()
                    self.device.queue_request(request)
            if image is not None:
                return image
        raise RuntimeError('Camera capture timed out or stopped')

    def stop(self):
        if self.running:
            self.running = False
            self.device.stop()

    def close(self):
        self.stop()
        self.selector.close()
        self.requests.clear()
        for mapping in self.maps:
            mapping.close()
        self.maps.clear()
        if hasattr(self, 'allocator'):
            self.buffers = []
            del self.allocator
        self.device.release()

# SPDX-License-Identifier: MIT
"""Minimal seekable MJPEG AVI muxer. Frames are existing JPEG snapshots."""
import struct


def chunk(tag, payload):
    return tag + struct.pack('<I', len(payload)) + payload + b'\0'*(len(payload)%2)


class MjpegAVI:
    def __init__(self, path, width=640, height=480, fps=10):
        self.file = open(path, 'w+b')
        self.width, self.height, self.fps = width, height, fps
        self.frames = []
        self.file.write(self._header(0, 0))
        self.movi = self.file.tell()-4

    def _header(self, count, movi_size):
        avih = struct.pack('<14I', int(1e6/self.fps), 0, 0, 0x10, count, 0, 1,
                           self.width*self.height*3, self.width, self.height, 0, 0, 0, 0)
        strh = struct.pack('<4s4sIHH8I4h', b'vids', b'MJPG', 0, 0, 0, 0, 1, self.fps,
                           0, count, self.width*self.height*3, 0xffffffff, 0, 0, 0, self.width, self.height)
        strf = struct.pack('<IiiHH4sIiiII', 40, self.width, self.height, 1, 24, b'MJPG',
                           self.width*self.height*3, 0, 0, 0, 0)
        hdrl = chunk(b'LIST', b'hdrl' + chunk(b'avih', avih) + chunk(b'LIST', b'strl'+chunk(b'strh',strh)+chunk(b'strf',strf)))
        return b'RIFF'+struct.pack('<I', 0)+b'AVI '+hdrl+b'LIST'+struct.pack('<I',movi_size+4)+b'movi'

    def write(self, jpeg):
        offset = self.file.tell()-self.movi
        self.file.write(chunk(b'00dc', jpeg))
        self.frames.append((offset, len(jpeg)))

    def close(self):
        end_movi = self.file.tell()
        index = b''.join(struct.pack('<4sIII',b'00dc',0x10,offset,size) for offset,size in self.frames)
        self.file.write(chunk(b'idx1',index))
        end = self.file.tell()
        self.file.seek(0)
        self.file.write(self._header(len(self.frames),end_movi-self.movi-4))
        self.file.seek(4)
        self.file.write(struct.pack('<I',end-8))
        self.file.close()

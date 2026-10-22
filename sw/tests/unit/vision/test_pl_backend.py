import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from ec_26_e.vision.preprocess import CpuPreprocessor
from ec_26_e.vision.pl import PynqPreprocessor

class Buffer(np.ndarray):
    def freebuffer(self): self.freed=True
    def invalidate(self): self.invalidated=True

class FakeHardware:
    def __init__(self, fault=None):
        self.fault=fault; self.buffers=[];self.reg={};self.ready=False;self.reset=False
        self.dma=SimpleNamespace(mmio=SimpleNamespace(read=self.status,write=self.reset_dma),
            recvchannel=SimpleNamespace(transfer=self.receive),sendchannel=SimpleNamespace(transfer=self.send))
        self.ip=SimpleNamespace(read=self.read,write=self.write)

    def allocate(self,shape,dtype):
        b=np.zeros(shape,dtype=dtype).view(Buffer); b.freed=False;b.invalidated=False
        self.buffers.append(b);return b
    def overlay(self,*args,**kwargs):
        assert kwargs=={'download':False}
        return SimpleNamespace(axi_dma_0=self.dma,puzzle_preprocess_0=self.ip)
    def receive(self,b): self.rx=b;self.ready=False
    def write(self,k,v): self.reg[k]=v
    def read(self,k):
        if k==0:return 2 if self.ready else 0
        if k==0x40:return self.reg[0x38]+(1 if self.fault=='id' else 0)
        return self.reg.get(k,0)
    def send(self,b):
        assert hasattr(self,'rx')
        image=np.column_stack((b&255,(b>>8)&255,(b>>16)&255)).astype(np.uint8).reshape(self.reg[0x18],self.reg[0x10],3)
        self.rx[:]=CpuPreprocessor().process(image,self.reg[0x38]).reshape(-1)
        self.ready=self.fault!='timeout'
    def status(self,k):
        if self.reset and k in (0,0x30):return 0
        return 0x10 if self.fault=='dma' else (2 if self.ready else 0)
    def reset_dma(self,k,v):self.reset=True

class PlTest(unittest.TestCase):
    def test_reset_access_failure_retains_buffers(self):
        fake=FakeHardware('dma')
        def broken_reset(*args):
            raise OSError('MMIO unavailable')
        fake.dma.mmio.write=broken_reset
        with self.backend(fake):
            device=PynqPreprocessor('test.bit')
            with self.assertRaisesRegex(RuntimeError,'DMA'):
                device.process(np.zeros((8,8,3),np.uint8),1)
            self.assertTrue(all(not b.freed for b in fake.buffers))
            self.assertEqual(len(device._quarantined),2)
            device.close()
            self.assertTrue(all(not b.freed for b in fake.buffers))
    def backend(self,fake):
        return patch.dict(sys.modules,{'pynq':SimpleNamespace(Overlay=fake.overlay,allocate=fake.allocate)})
    def test_roundtrip_and_consecutive_frames(self):
        fake=FakeHardware()
        image=np.random.default_rng(42).integers(0,256,(32,41,3),dtype=np.uint8)
        with self.backend(fake):
            device=PynqPreprocessor('test.bit')
            for frame in range(1,4):
                np.testing.assert_array_equal(device.process(image,frame),CpuPreprocessor().process(image,frame))
            self.assertEqual(len(fake.buffers), 2)
            self.assertTrue(all(not b.freed for b in fake.buffers))
            device.close()
            self.assertTrue(all(b.freed for b in fake.buffers))
            device.close()
            with self.assertRaises(RuntimeError):device.process(image,4)
    def test_resize_and_noncontiguous_input_preserve_owned_output(self):
        fake=FakeHardware()
        image=np.random.default_rng(43).integers(0,256,(32,42,3),dtype=np.uint8)[:,::2]
        with self.backend(fake):
            device=PynqPreprocessor('test.bit')
            first=device.process(image,1)
            expected=first.copy()
            device.process(np.full_like(image,255),2)
            np.testing.assert_array_equal(first,expected)
            np.testing.assert_array_equal(expected,CpuPreprocessor().process(image,1))
            device.process(np.zeros((8,9,3),np.uint8),3)
            self.assertEqual(len(fake.buffers),4)
            self.assertTrue(all(b.freed for b in fake.buffers[:2]))
            self.assertTrue(all(not b.freed for b in fake.buffers[2:]))
            device.close()
            self.assertTrue(all(b.freed for b in fake.buffers))
    def test_receive_allocation_failure_releases_transmit_buffer(self):
        fake=FakeHardware()
        allocate=fake.allocate
        def fail_receive(shape,dtype):
            if dtype==np.uint8:
                raise MemoryError('CMA exhausted')
            return allocate(shape,dtype)
        fake.allocate=fail_receive
        with self.backend(fake):
            device=PynqPreprocessor('test.bit')
            with self.assertRaises(MemoryError):
                device.process(np.zeros((8,8,3),np.uint8),1)
            self.assertTrue(fake.buffers[0].freed)
            device.close()
    def test_faults_poison_backend_and_release_after_reset(self):
        for fault in ('timeout','id','dma'):
            with self.subTest(fault=fault):
                fake=FakeHardware(fault)
                with self.backend(fake):
                    device=PynqPreprocessor('test.bit',timeout_seconds=0.001)
                    with self.assertRaises((RuntimeError,TimeoutError)):device.process(np.zeros((8,8,3),np.uint8),1)
                    self.assertTrue(fake.reset)
                    self.assertTrue(all(b.freed for b in fake.buffers))
                    with self.assertRaises(RuntimeError):device.process(np.zeros((8,8,3),np.uint8),2)

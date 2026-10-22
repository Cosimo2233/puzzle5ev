"""Minimal lossless-property FDT reader/writer for local image preparation.

Only inline FIT payloads are supported. No active boot device is written.
"""
from dataclasses import dataclass, field
import struct


@dataclass
class Node:
    name: str
    props: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def child(self, name):
        return next(n for n in self.children if n.name == name)

    def at(self, path):
        node = self
        for part in path.strip('/').split('/'):
            if part: node = node.child(part)
        return node

    def walk(self):
        yield self
        for node in self.children: yield from node.walk()


class Fdt:
    def __init__(self, blob):
        if len(blob) < 40: raise ValueError('Truncated FDT header')
        magic, total, off_struct, off_strings, off_mem, version, compat, cpu, nstrings, nstruct = struct.unpack_from('>10I', blob)
        if magic != 0xd00dfeed or version != 17 or compat > 17 or total > len(blob):
            raise ValueError('Unsupported or truncated FDT')
        if off_struct + nstruct > total or off_strings + nstrings > total:
            raise ValueError('FDT blocks outside declared size')
        self.cpu = cpu
        self.reservations = []
        while True:
            if off_mem + 16 > total: raise ValueError('Truncated reservation map')
            item = struct.unpack_from('>QQ', blob, off_mem); off_mem += 16
            if item == (0, 0): break
            self.reservations.append(item)
        strings = blob[off_strings:off_strings+nstrings]
        data = blob[off_struct:off_struct+nstruct]
        offset = 0
        stack = []
        self.root = None
        while offset < len(data):
            token, = struct.unpack_from('>I', data, offset); offset += 4
            if token == 1:
                end = data.index(0, offset)
                node = Node(data[offset:end].decode('ascii'))
                offset = (end + 4) & ~3
                if stack: stack[-1].children.append(node)
                elif self.root is None: self.root = node
                else: raise ValueError('Multiple roots')
                stack.append(node)
            elif token == 2:
                if not stack: raise ValueError('Unbalanced node')
                stack.pop()
            elif token == 3:
                length, nameoff = struct.unpack_from('>II', data, offset); offset += 8
                if not stack or offset + length > len(data): raise ValueError('Invalid property')
                name = strings[nameoff:strings.index(0, nameoff)].decode('ascii')
                if name in stack[-1].props: raise ValueError('Duplicate property')
                stack[-1].props[name] = data[offset:offset+length]
                offset = (offset + length + 3) & ~3
            elif token == 4: continue
            elif token == 9:
                if stack or self.root is None: raise ValueError('Unclosed FDT')
                return
            else: raise ValueError(f'Invalid FDT token {token}')
        raise ValueError('Missing FDT end')

    def pack(self):
        strings = bytearray(); names = {}
        for node in self.root.walk():
            for name in node.props:
                if name not in names:
                    names[name] = len(strings); strings.extend(name.encode('ascii') + b'\0')
        data = bytearray()
        def word(value): data.extend(struct.pack('>I', value))
        def padded(value):
            data.extend(value); data.extend(b'\0' * (-len(data) % 4))
        def emit(node):
            word(1); padded(node.name.encode('ascii') + b'\0')
            for name, value in node.props.items():
                word(3); word(len(value)); word(names[name]); padded(value)
            for child in node.children: emit(child)
            word(2)
        emit(self.root); word(9)
        reserved = b''.join(struct.pack('>QQ', *p) for p in self.reservations + [(0, 0)])
        start = 40 + len(reserved)
        header = struct.pack('>10I', 0xd00dfeed, start+len(data)+len(strings), start,
                             start+len(data), 40, 17, 16, self.cpu, len(strings), len(data))
        return header + reserved + data + strings

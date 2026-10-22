"""Boot image safety: preserve payloads and reject unverifiable inputs."""
import hashlib
import importlib
from pathlib import Path
import struct

import pytest


@pytest.fixture
def boot(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root/'scripts/axu5ev'))
    return importlib.import_module('prepare_boot'), importlib.import_module('fdt')


def fixture_fit(module):
    def tree(root):
        dt = object.__new__(module.Fdt)
        dt.root, dt.cpu, dt.reservations = root, 0, [(0x12340000,0x1000)]
        return dt
    N = module.Node
    dt = tree(N('', children=[N('amba_pl@0', children=[N('dma@80040000')]),
                              N('amba',children=[N('i2c@ff020000'),N('ethernet@ff0e0000',{'status':b'okay\0'})]),
                              N('chosen',{'bootargs':b'original arguments\0'})]))
    def image(name, kind, data):
        return N(name,{'type':kind+b'\0','data':data,'compression':b'none\0'},
                 [N('hash@1',{'algo':b'sha256\0','value':hashlib.sha256(data).digest()})])
    return tree(N('',children=[N('images',children=[image('kernel@1',b'kernel',b'unchanged kernel\x00\xff'),
                                                       image('fdt@1',b'flat_dt',dt.pack())]),
                                     N('configurations',{'default':b'conf@1\0'},[N('conf@1',{'kernel':b'kernel@1\0','fdt':b'fdt@1\0'})])]))


def test_prepare_preserves_kernel_bootargs_ps_and_reservations(boot):
    prepare, module = boot
    original = fixture_fit(module)
    data, records = prepare.prepare(original.pack())
    result = module.Fdt(data)
    assert result.root.at('/images/kernel@1').props == original.root.at('/images/kernel@1').props
    assert result.root.at('/configurations').props == original.root.at('/configurations').props
    dt = module.Fdt(result.root.at('/images/fdt@1').props['data'])
    assert dt.reservations == original.reservations
    assert dt.root.at('/chosen').props['bootargs'] == b'original arguments\0'
    assert dt.root.at('/chosen').props['ec26e,pl-base'] == b'axu5ev-v1\0'
    assert dt.root.at('/amba/ethernet@ff0e0000').props['status'] == b'okay\0'
    for path in ('/amba_pl@0','/amba_pl@0/dma@80040000','/amba/i2c@ff020000'):
        assert dt.root.at(path).props['status'] == b'disabled\0'
    assert [r['changed'] for r in records] == [False,True]


def test_usb_phy_candidate_resolves_lane_from_base_tree(boot):
    prepare, module = boot
    original = fixture_fit(module)
    image = original.root.at('/images/fdt@1')
    dt = module.Fdt(image.props['data'])
    N = module.Node
    dt.root.at('/amba').children.extend([
        N('zynqmp_phy@fd400000', children=[N('lane1', {'phandle': struct.pack('>I', 107)})]),
        N('usb0@ff9d0000', children=[N('dwc3@fe200000', {'dr_mode': b'host\0'})]),
    ])
    image.props['data'] = dt.pack()
    image.children[0].props['value'] = hashlib.sha256(image.props['data']).digest()
    result, _ = prepare.prepare(original.pack(), usb_phy=True)
    updated = module.Fdt(module.Fdt(result).root.at('/images/fdt@1').props['data'])
    usb = updated.root.at('/amba/usb0@ff9d0000/dwc3@fe200000')
    assert usb.props['phys'] == struct.pack('>5I', 107, 4, 0, 1, 26000000)
    assert usb.props['phy-names'] == b'usb3-phy\0'
    assert usb.props['dr_mode'] == b'host\0'


@pytest.mark.parametrize('bad', ['checksum','signature','external'])
def test_prepare_rejects_unverifiable_image(boot, bad):
    prepare, module = boot
    image = fixture_fit(module)
    kernel = image.root.at('/images/kernel@1')
    if bad == 'checksum': kernel.props['data'] += b'corrupted'
    elif bad == 'signature': kernel.children.append(module.Node('signature@1'))
    else: kernel.props['data-position'] = b'\0'*4
    with pytest.raises(ValueError): prepare.prepare(image.pack())

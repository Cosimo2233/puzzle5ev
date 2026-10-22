import numpy as np

from ec_26_e.gui.preview_tracking import PreviewContours


def report(offset=0):
    first = np.array([[10, 10], [40, 10], [40, 50], [10, 50]], float) + offset
    return dict(valid=True, contours_source_px=[first.tolist(), (first+[100, 0]).tolist()])


def test_small_jitter_is_damped_without_mutating_observation():
    tracker = PreviewContours()
    tracker.update(report())
    raw = report(1)
    output = tracker.update(raw)
    np.testing.assert_allclose(output['contours_source_px'][0],
                               np.array(report()['contours_source_px'][0])+.45)
    assert raw == report(1)


def test_reordered_vertices_and_components_keep_their_display_identity():
    tracker = PreviewContours()
    tracker.update(report())
    other = report()
    other['contours_source_px'] = [np.roll(p, 2, axis=0).tolist()
                                  for p in other['contours_source_px'][::-1]]
    assert tracker.update(other) == report()


def test_real_motion_invalid_detection_and_topology_change_reset_smoothing():
    tracker = PreviewContours()
    tracker.update(report())
    assert tracker.update(report(20)) == report(20)
    invalid = dict(valid=False, contours_source_px=[])
    assert tracker.update(invalid) == invalid
    assert tracker.update(report(21)) == report(21)
    changed = report(21)
    changed['contours_source_px'][0].pop()
    assert tracker.update(changed) == changed

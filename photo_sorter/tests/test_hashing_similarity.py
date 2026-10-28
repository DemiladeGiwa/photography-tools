import cv2
import numpy as np

from photo_sorter.core import hashing
from photo_sorter.core.similarity import BKTree, histogram_similarity, histogram_signature
from .conftest import make_sharp_image, make_blurry_image, make_noisy_image


def test_hash_identical_image_has_zero_distance():
    img = make_sharp_image()
    assert hashing.hamming(hashing.phash(img), hashing.phash(img)) == 0
    assert hashing.hamming(hashing.dhash(img), hashing.dhash(img)) == 0


def test_hash_near_duplicate_small_distance():
    img = make_sharp_image()
    resized = cv2.resize(img, (380, 380))
    resized = cv2.resize(resized, (img.shape[1], img.shape[0]))
    d = hashing.hamming(hashing.phash(img), hashing.phash(resized))
    assert d <= 12  # small perturbation should stay close in hash space


def test_hash_unrelated_images_are_far_apart():
    a = make_sharp_image(seed=1)
    b = make_sharp_image(seed=99)
    d = hashing.hamming(hashing.phash(a), hashing.phash(b))
    assert d >= 8


def test_bktree_query_returns_only_within_radius():
    tree: BKTree[str] = BKTree()
    base = 0b1010101010101010
    tree.add(base, "self")
    tree.add(base ^ 0b1, "one_bit")            # distance 1
    tree.add(base ^ 0b1111, "four_bits")        # distance 4
    tree.add(base ^ 0xFFFFFFFF, "far")           # many bits away

    results = tree.query(base, max_distance=4)
    names = {item for _, item in results}
    assert names == {"self", "one_bit", "four_bits"}
    assert "far" not in names


def test_bktree_scales_without_all_pairs():
    """Not a perf benchmark — just a sanity check that a BK-tree over many
    hashes still finds a planted near-duplicate."""
    rng = np.random.default_rng(0)
    tree: BKTree[int] = BKTree()
    target = 123456789
    for i in range(2000):
        tree.add(int(rng.integers(0, 2**63)), i)
    tree.add(target, -1)
    tree.add(target ^ 0b111, -2)  # near duplicate, distance 3
    results = tree.query(target, max_distance=5)
    ids = {item for _, item in results}
    assert -1 in ids and -2 in ids


def test_histogram_similarity_higher_for_similar_colors():
    a = make_sharp_image(seed=5)
    a_bgr = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    b_bgr = a_bgr.copy()  # identical color distribution
    c_bgr = cv2.cvtColor(make_sharp_image(seed=5) // 3, cv2.COLOR_GRAY2BGR)  # much darker

    sim_same = histogram_similarity(histogram_signature(a_bgr), histogram_signature(b_bgr))
    sim_diff = histogram_similarity(histogram_signature(a_bgr), histogram_signature(c_bgr))
    assert sim_same > sim_diff


def test_histogram_serialization_roundtrip():
    from photo_sorter.core.similarity import hist_to_list, list_to_hist

    a = make_sharp_image(seed=7)
    a_bgr = cv2.cvtColor(a, cv2.COLOR_GRAY2BGR)
    hist = histogram_signature(a_bgr)

    as_list = hist_to_list(hist)
    assert isinstance(as_list, list)
    assert len(as_list) == 8 * 8 * 8

    reconstructed = list_to_hist(as_list)
    assert np.allclose(hist, reconstructed, atol=1e-5)
    assert histogram_similarity(hist, reconstructed) >= 0.999


import tempfile
import numpy as np
from pathlib import Path
from cardiotensor.utils.utils import convert_to_8bit
from cardiotensor.orientation.orientation_computation_functions import (  # Adjust the import to match the actual module name
    interpolate_points,
    calculate_center_vector,
    adjust_start_end_index,
    calculate_structure_tensor,
    remove_padding,
    compute_fraction_anisotropy,
    rotate_vectors_to_new_axis,
    compute_helix_and_transverse_angles,
    write_images,
    write_vector_field,
)


def test_interpolate_points():
    point1 = (0, 0, 0)
    point2 = (10, 10, 10)
    interpolated = interpolate_points(point1, point2, N_img=11)
    assert interpolated.shape == (11, 3)
    print("test_interpolate_points passed.")


def test_calculate_center_vector():
    pt_mv = np.array([10.0, 10.0, 10.0])
    pt_apex = np.array([0.0, 0.0, 0.0])
    center_vector = calculate_center_vector(pt_mv, pt_apex)
    assert np.allclose(np.linalg.norm(center_vector), 1)
    print("test_calculate_center_vector passed.")


def test_adjust_start_end_index():
    start, end = adjust_start_end_index(10, 20, 50, 5, 5, is_test=False, n_slice=0)
    assert start == 5 and end == 25
    print("test_adjust_start_end_index passed.")


def test_calculate_structure_tensor():
    volume = np.random.rand(50, 50, 50).astype(np.float32)
    SIGMA, RHO = 1.0, 2.0
    val, vec = calculate_structure_tensor(volume, SIGMA, RHO, use_gpu=False)
    assert val.shape[1:] == volume.shape
    assert vec.shape[1:] == volume.shape
    print("test_calculate_structure_tensor passed.")


def test_remove_padding():
    volume = np.random.rand(10, 50, 50)
    val = np.random.rand(3, 10, 50, 50)
    vec = np.random.rand(3, 10, 50, 50)
    volume, val, vec = remove_padding(volume, val, vec, padding_start=2, padding_end=2)
    assert volume.shape[0] == 6
    assert val.shape[1] == 6
    assert vec.shape[1] == 6
    print("test_remove_padding passed.")


def test_compute_fraction_anisotropy():
    eigenvalues = np.random.rand(3, 50, 50)
    FA = compute_fraction_anisotropy(eigenvalues)
    assert FA.shape == (50, 50)
    print("test_compute_fraction_anisotropy passed.")


def test_rotate_vectors_to_new_axis():
    vector_field_slice = np.random.rand(3, 100)
    new_axis_vec = np.array([0, 0, 1])
    rotated = rotate_vectors_to_new_axis(vector_field_slice, new_axis_vec)
    assert rotated.shape == vector_field_slice.shape
    print("test_rotate_vectors_to_new_axis passed.")


def test_compute_helix_and_transverse_angles():
    vector_field_2d = np.random.rand(3, 100, 100)
    center_point = (50, 50, 50)
    helix_angle, transverse_angle = compute_helix_and_transverse_angles(
        vector_field_2d, center_point
    )
    assert helix_angle.shape == transverse_angle.shape == (100, 100)
    print("test_compute_helix_and_transverse_angles passed.")


def test_write_images():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        img_helix = np.random.rand(100, 100)
        img_intrusion = np.random.rand(100, 100)
        img_FA = np.random.rand(100, 100)
        write_images(
            img_helix,
            img_intrusion,
            img_FA,
            start_index=0,
            OUTPUT_DIR=str(temp_dir),
            OUTPUT_FORMAT="tif",
            OUTPUT_TYPE="8bit",
            z=0,
        )
        assert (temp_dir / "HA/HA_000000.tif").exists()
        assert (temp_dir / "IA/IA_000000.tif").exists()
        assert (temp_dir / "FA/FA_000000.tif").exists()
        print("test_write_images passed.")


def test_write_vector_field():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        vector_field_slice = np.random.rand(3, 50, 50)
        write_vector_field(
            vector_field_slice, start_index=0, output_dir=str(temp_dir), slice_idx=0
        )
        assert (temp_dir / "eigen_vec/eigen_vec_000000.npy").exists()
        print("test_write_vector_field passed.")


if __name__ == "__main__":
    test_interpolate_points()
    test_calculate_center_vector()
    test_adjust_start_end_index()
    test_calculate_structure_tensor()
    test_remove_padding()
    test_compute_fraction_anisotropy()
    test_rotate_vectors_to_new_axis()
    test_compute_helix_and_transverse_angles()
    test_write_images()
    test_write_vector_field()

import math
import multiprocessing as mp
import os
import sys
import time

import cv2
import dask_image.imread
import numpy as np
from alive_progress import alive_bar
from scipy.ndimage import zoom

from cardiotensor.orientation.orientation_computation_functions import (
    adjust_start_end_index,
    calculate_center_vector,
    calculate_structure_tensor,
    compute_fraction_anisotropy,
    compute_helix_and_transverse_angles,
    interpolate_points,
    plot_images,
    remove_padding,
    rotate_vectors_to_new_axis,
    write_images,
    write_vector_field,
)
from cardiotensor.utils.utils import (
    get_image_list,
    load_raw_data_with_mhd,
    load_volume,
    read_conf_file,
    read_mhd,
)

MULTIPROCESS = True


def is_tiff_image_valid(image_path: str) -> bool:
    """
    Check if the TIFF image is readable and valid.

    Args:
        image_path (str): Path to the TIFF image.

    Returns:
        bool: True if the image is valid, False otherwise.
    """

    try:
        image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(
                "cv2.imread failed to load the image. Unsupported or corrupted TIFF file."
            )
        return True
    except Exception as e:
        print(f"Error reading TIFF image {image_path}: {e}")
        return False


def compute_orientation(
    conf_file_path: str, start_index: int = 0, end_index: int = 0, use_gpu: bool = False
) -> None:
    """
    Compute the orientation for a volume dataset based on the configuration.

    Args:
        conf_file_path (str): Path to the configuration file.
        start_index (int, optional): Start index for processing. Default is 0.
        end_index (int, optional): End index for processing. Default is 0.
        use_gpu (bool, optional): Whether to use GPU for calculations. Default is False.

    Returns:
        None
    """

    print("\n---------------------------------")
    print(f"🤖 - Processing slices {start_index} to {end_index}")

    print("\n---------------------------------")
    print(f"READING PARAMETER FILE : {conf_file_path}\n")

    print(f"Start index, End index : {start_index}, {end_index}\n")

    try:
        params = read_conf_file(conf_file_path)
    except Exception as e:
        print(e)
        sys.exit(f"⚠️  Error reading parameter file: {conf_file_path}")

    (
        VOLUME_PATH,
        MASK_PATH,
        IS_FLIP,
        OUTPUT_DIR,
        OUTPUT_FORMAT,
        OUTPUT_TYPE,
        IS_VECTORS,
        SIGMA,
        RHO,
        N_CHUNK,
        PT_MV,
        PT_APEX,
        IS_TEST,
        N_SLICE_TEST,
    ) = (
        params[key]
        for key in [
            "IMAGES_PATH",
            "MASK_PATH",
            "FLIP",
            "OUTPUT_PATH",
            "OUTPUT_FORMAT",
            "OUTPUT_TYPE",
            "VECTORS",
            "SIGMA",
            "RHO",
            "N_CHUNK",
            "POINT_MITRAL_VALVE",
            "POINT_APEX",
            "TEST",
            "N_SLICE_TEST",
        ]
    )

    if not IS_TEST:
        is_already_done = True
        if end_index == 0:
            is_already_done = False
        for idx in range(start_index, end_index):
            if (
                not os.path.exists(f"{OUTPUT_DIR}/HA/HA_{idx:06d}.tif")
                or not os.path.exists(f"{OUTPUT_DIR}/IA/IA_{idx:06d}.tif")
                or not os.path.exists(f"{OUTPUT_DIR}/FA/FA_{idx:06d}.tif")
                or not os.path.exists(f"{OUTPUT_DIR}/eigen_vec/eigen_vec_{idx:06d}.npy")
            ):
                is_already_done = False
                break

        if is_already_done:
            print("All images are already done")
            return

    if MASK_PATH:
        is_mask = True
    else:
        print("Mask path not provided")
        is_mask = False

    print("\n---------------------------------")
    print("READING VOLUME INFORMATION\n")
    print(f"Volume path: {VOLUME_PATH}")

    if VOLUME_PATH[-4:] == ".mhd":
        img_type = "mhd"
        meta_dict = read_mhd(VOLUME_PATH)
        N_img = meta_dict["DimSize"][2]

    elif os.path.isdir(VOLUME_PATH):
        img_list, img_type = get_image_list(VOLUME_PATH)
        N_img = len(img_list)
        print(f"{N_img} {img_type} files found\n")

        print("Reading images with Dask...")
        volume_dask = dask_image.imread.imread(f"{VOLUME_PATH}/*.{img_type}")
        print(f"Dask volume: {volume_dask}")

    print(f"Number of slices: {N_img}")

    print("\n---------------------------------")
    print("CALCULATE CENTER LINE AND CENTER VECTOR\n")
    center_line = interpolate_points(PT_MV, PT_APEX, N_img)
    center_vec = calculate_center_vector(PT_MV, PT_APEX, IS_FLIP)
    print(f"Center vector: {center_vec}")

    print("\n---------------------------------")
    print("CALCULATE PADDING START AND ENDING INDEXES\n")
    padding_start = math.ceil(RHO)
    padding_end = math.ceil(RHO)
    if not IS_TEST:
        if padding_start > start_index:
            padding_start = start_index
        if padding_end > (N_img - end_index):
            padding_end = N_img - end_index
    if IS_TEST:
        if N_SLICE_TEST > N_img:
            sys.exit("Error: N_SLICE_TEST > number of images")

    print(f"Padding start, Padding end : {padding_start}, {padding_end}")
    start_index_padded, end_index_padded = adjust_start_end_index(
        start_index, end_index, N_img, padding_start, padding_end, IS_TEST, N_SLICE_TEST
    )
    print(
        f"Start index padded, End index padded : {start_index_padded}, {end_index_padded}"
    )

    print("\n---------------------------------")
    print("LOAD DATASET\n")
    if VOLUME_PATH[-4:] == ".mhd":
        # Read the binary data from the .raw file
        volume, _ = load_raw_data_with_mhd(VOLUME_PATH)
        volume = volume[start_index_padded:end_index_padded, :, :].astype("float32")

    elif os.path.isdir(VOLUME_PATH):
        volume = load_volume(img_list, start_index_padded, end_index_padded).astype(
            "float32"
        )

    print(f"Loaded volume shape {volume.shape}")

    if is_mask:
        print("\n---------------------------------")
        print("LOADING MASK\n")
        print(f"Reading mask info from {MASK_PATH}...")

        mask_list, mask_type = get_image_list(MASK_PATH)
        if len(mask_list) == 0:
            sys.exit("No mask images found - verify your path")
        print(f"{len(mask_list)} {mask_type} files found\n")
        # mask_dask = dask_image.imread.imread(f'{MASK_PATH}/*.{mask_type}')
        # print(f"Dask mask: {mask_dask}")
        N_mask = len(mask_list)
        binning_factor = N_img / N_mask
        print(f"Mask bining factor: {binning_factor}\n")

        start_index_padded_mask = int(
            (start_index_padded / binning_factor) - 1
        )  # -binning_factor/2)
        if start_index_padded_mask < 0:
            start_index_padded_mask = 0
        end_index_padded_mask = int(
            (end_index_padded / binning_factor) + 1
        )  # +binning_factor/2)
        if end_index_padded_mask > N_mask:
            end_index_padded_mask = N_mask

        print(
            f"Mask start index padded: {start_index_padded_mask} - Mask end index padded : {end_index_padded_mask}"
        )

        mask = load_volume(mask_list, start_index_padded_mask, end_index_padded_mask)
        print(f"Mask volume loaded of shape {mask.shape}")

        mask = zoom(mask, binning_factor, order=0)

        # start_index_mask_upscaled = int(mask.shape[0]/2)-int(volume.shape[0]/2)
        # end_index_mask_upscaled = start_index_mask_upscaled+volume.shape[0]

        start_index_mask_upscaled = int(
            np.abs(start_index_padded_mask * binning_factor - start_index_padded)
        )
        end_index_mask_upscaled = start_index_mask_upscaled + volume.shape[0]

        if start_index_mask_upscaled < 0:
            start_index_mask_upscaled = 0
        if end_index_mask_upscaled > mask.shape[0]:
            end_index_mask_upscaled = mask.shape[0]

        mask = mask[start_index_mask_upscaled:end_index_mask_upscaled, :]

        mask_resized = np.empty_like(volume)
        for i in range(mask.shape[0]):
            # Resize the slice to match the corresponding slice of the volume
            mask_resized[i] = cv2.resize(
                mask[i],
                (volume.shape[2], volume.shape[1]),
                interpolation=cv2.INTER_LINEAR,
            )

        mask = mask_resized
        del mask_resized

        assert (
            mask.shape == volume.shape
        ), f"Mask shape {mask.shape} does not match volume shape {volume.shape}"

        volume[mask == 0] = 0

        print("Mask applied to image volume")

    print("\n---------------------------------")
    print("CALCULATING STRUCTURE TENSOR")
    t1 = time.perf_counter()  # start time
    s, val, vec = calculate_structure_tensor(volume, SIGMA, RHO, use_gpu=use_gpu)
    print(f"Vector shape: {vec.shape}")

    if is_mask:
        volume[mask == 0] = np.nan
        val[0, :, :, :][mask == 0] = np.nan
        val[1, :, :, :][mask == 0] = np.nan
        val[2, :, :, :][mask == 0] = np.nan
        vec[0, :, :, :][mask == 0] = np.nan
        vec[1, :, :, :][mask == 0] = np.nan
        vec[2, :, :, :][mask == 0] = np.nan

        print("Mask applied to image volume")

        del mask

    volume, _, val, vec = remove_padding(
        volume, s, val, vec, padding_start, padding_end
    )
    del s
    print(f"Vector shape after removing padding: {vec.shape}")

    center_line = center_line[start_index_padded:end_index_padded]

    # Putting all the vectors in positive direction
    # posdef = np.all(val >= 0, axis=0)  # Check if all elements are non-negative along the first axis
    vec_norm = np.linalg.norm(vec, axis=0)
    vec = vec / (vec_norm)

    # Check for negative z component and flip if necessary
    # negative_z = vec[2, :] < 0
    # vec[:, negative_z] *= -1

    t2 = time.perf_counter()  # stop time
    print(f"finished calculating structure tensors in {t2 - t1} seconds")

    print("\nCalculating helix/intrusion angle and fractional anisotropy:")
    if MULTIPROCESS and not IS_TEST:
        print(f"Using {mp.cpu_count()} CPU cores")
        with mp.Pool(processes=mp.cpu_count()) as pool:
            # Initialize the alive-progress bar
            with alive_bar(
                vec.shape[1], title="Processing slices (Multiprocess)", bar="smooth"
            ) as bar:
                result = pool.starmap_async(
                    compute_slice_angles_and_anisotropy,
                    [
                        (
                            z,
                            vec[:, z, :, :],
                            volume[z, :, :],
                            np.around(center_line[z]),
                            val[:, z, :, :],
                            center_vec,
                            OUTPUT_DIR,
                            OUTPUT_FORMAT,
                            OUTPUT_TYPE,
                            start_index,
                            IS_VECTORS,
                            IS_TEST,
                        )
                        for z in range(vec.shape[1])
                    ],
                    callback=lambda _: bar(
                        vec.shape[1]
                    ),  # Update progress bar once all tasks are completed
                )
                result.wait()  # Wait for all tasks to complete

    else:
        # Add a progress bar for single-threaded processing
        with alive_bar(
            vec.shape[1], title="Processing slices (Single-thread)", bar="smooth"
        ) as bar:
            for z in range(vec.shape[1]):
                # Call the function directly
                compute_slice_angles_and_anisotropy(
                    z,
                    vec[:, z, :, :],
                    volume[z, :, :],
                    np.around(center_line[z]),
                    val[:, z, :, :],
                    center_vec,
                    OUTPUT_DIR,
                    OUTPUT_FORMAT,
                    OUTPUT_TYPE,
                    start_index,
                    IS_VECTORS,
                    IS_TEST,
                )
                bar()  # Update the progress bar for each slice

    # #Check images
    # for idx in range(start_index, end_index):
    #     ia_path = f"{OUTPUT_DIR}/IA/IA_{idx:06d}.tif"
    #     ha_path = f"{OUTPUT_DIR}/HA/HA_{idx:06d}.tif"
    #     fa_path = f"{OUTPUT_DIR}/FA/FA_{idx:06d}.tif"

    #     # Validate IA image
    #     if not is_tiff_image_valid(ia_path):
    #         print(f"Invalid or corrupt IA image: {ia_path}")
    #         if os.path.exists(ia_path):
    #             os.remove(ia_path)
    #         continue

    #     # Validate HA image
    #     if not is_tiff_image_valid(ha_path):
    #         print(f"Invalid or corrupt HA image: {ha_path}")
    #         if os.path.exists(ha_path):
    #             os.remove(ha_path)
    #         continue

    #     # Validate FA image
    #     if not is_tiff_image_valid(fa_path):
    #         print(f"Invalid or corrupt FA image: {fa_path}")
    #         if os.path.exists(fa_path):
    #             os.remove(fa_path)
    #         continue

    #     print(f"Valid image set: {idx:06d}")

    print(f"\n🤖 - Finished processing slices {start_index} - {end_index}")
    print("---------------------------------\n\n")

    return


def compute_slice_angles_and_anisotropy(
    z: int,
    vector_field_slice: np.ndarray,
    img_slice: np.ndarray,
    center_point: np.ndarray,
    eigen_val_slice: np.ndarray,
    center_vec: np.ndarray,
    OUTPUT_DIR: str,
    OUTPUT_FORMAT: str,
    OUTPUT_TYPE: str,
    start_index: int,
    IS_VECTORS: bool,
    IS_TEST: bool,
) -> None:
    """
    Compute helix angles, transverse angles, and fractional anisotropy for a slice.

    Args:
        z (int): Index of the slice.
        vector_field_slice (np.ndarray): Vector field for the slice.
        img_slice (np.ndarray): Image data for the slice.
        center_point (np.ndarray): Center point for alignment.
        eigen_val_slice (np.ndarray): Eigenvalues for the slice.
        center_vec (np.ndarray): Center vector for alignment.
        OUTPUT_DIR (str): Directory to save the output.
        OUTPUT_FORMAT (str): Format for the output files (e.g., "tif").
        OUTPUT_TYPE (str): Type of output (e.g., "HA", "IA").
        start_index (int): Start index of the slice.
        IS_VECTORS (bool): Whether to output vector fields.
        IS_TEST (bool): Whether in test mode.

    Returns:
        None
    """
    # print(f"Processing image: {start_index + z}")
    paths = [
        f"{OUTPUT_DIR}/HA/HA_{(start_index + z):06d}.tif",
        f"{OUTPUT_DIR}/IA/IA_{(start_index + z):06d}.tif",
        f"{OUTPUT_DIR}/FA/FA_{(start_index + z):06d}.tif",
    ]
    if not IS_TEST and all(os.path.exists(path) for path in paths):
        # print(f"File {(start_index + z):06d} already exists")
        return

    img_FA = compute_fraction_anisotropy(eigen_val_slice)
    vector_field_slice_rotated = rotate_vectors_to_new_axis(
        vector_field_slice, center_vec
    )
    img_helix, img_intrusion = compute_helix_and_transverse_angles(
        vector_field_slice_rotated, center_point
    )

    if IS_TEST:
        plot_images(img_slice, img_helix, img_intrusion, img_FA, center_point)
    else:
        write_images(
            img_helix,
            img_intrusion,
            img_FA,
            start_index,
            OUTPUT_DIR,
            OUTPUT_FORMAT,
            OUTPUT_TYPE,
            z,
        )
        if IS_VECTORS:
            write_vector_field(vector_field_slice, start_index, OUTPUT_DIR, z)

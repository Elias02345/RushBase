from rushbase_core import AssetGroup, FileRole, MediaType, group_files

B = "Input/2026-10-12_Dreh"


def roles(group: AssetGroup) -> dict[str, FileRole]:
    return {m.rel_path.rsplit("/", 1)[-1]: m.role for m in group.members}


def test_raw_jpeg_xmp_form_one_asset() -> None:
    result = group_files(
        [f"{B}/IMG_0001.CR3", f"{B}/IMG_0001.JPG", f"{B}/IMG_0001.xmp", f"{B}/IMG_0002.JPG"],
        batch_root=B,
    )
    assert len(result.groups) == 2
    first = result.groups[0]
    assert first.media_type is MediaType.PHOTO
    assert first.primary.rel_path == f"{B}/IMG_0001.CR3"
    assert roles(first) == {
        "IMG_0001.CR3": FileRole.RAW,
        "IMG_0001.JPG": FileRole.JPEG,
        "IMG_0001.xmp": FileRole.SIDECAR,
    }
    assert roles(result.groups[1]) == {"IMG_0002.JPG": FileRole.PRIMARY}


def test_stem_matching_is_case_insensitive_and_directory_local() -> None:
    result = group_files([f"{B}/a/img_1.jpg", f"{B}/a/IMG_1.CR2", f"{B}/b/IMG_1.JPG"], batch_root=B)
    assert sorted(len(g.members) for g in result.groups) == [1, 2]


def test_drone_video_with_proxy_telemetry_and_thumbnail() -> None:
    files = [f"{B}/DJI_0007.MP4", f"{B}/DJI_0007.LRF", f"{B}/DJI_0007.SRT", f"{B}/DJI_0007.THM"]
    (group,) = group_files(files, batch_root=B).groups
    assert group.media_type is MediaType.VIDEO
    assert roles(group) == {
        "DJI_0007.MP4": FileRole.PRIMARY,
        "DJI_0007.LRF": FileRole.PROXY_CAMERA,
        "DJI_0007.SRT": FileRole.TELEMETRY,
        "DJI_0007.THM": FileRole.SIDECAR,
    }
    assert group.card_structure_root is None


def test_orphan_sidecar_stays_ungrouped_until_partner_arrives() -> None:
    result = group_files([f"{B}/IMG_9.xmp"], batch_root=B)
    assert result.groups == ()
    assert result.ungrouped == (f"{B}/IMG_9.xmp",)


def test_unknown_file_becomes_other_asset() -> None:
    (group,) = group_files([f"{B}/notes.pdf"], batch_root=B).groups
    assert group.media_type is MediaType.OTHER


def test_sony_card_structure_one_asset_per_clip_sharing_the_root() -> None:
    card = f"{B}/A001"
    files = [
        f"{card}/PRIVATE/M4ROOT/CLIP/C0001.MP4",
        f"{card}/PRIVATE/M4ROOT/CLIP/C0001M01.XML",
        f"{card}/PRIVATE/M4ROOT/CLIP/C0002.MP4",
        f"{card}/PRIVATE/M4ROOT/SUB/C0001S03.MP4",
        f"{card}/PRIVATE/M4ROOT/MEDIAPRO.XML",
        f"{card}/PRIVATE/M4ROOT/GENERAL/SONYCARD.IND",
    ]
    result = group_files(files, batch_root=B)
    assert [g.card_structure_root for g in result.groups] == [card, card]
    first, second = result.groups
    assert roles(first)["C0001S03.MP4"] is FileRole.PROXY_CAMERA
    assert roles(first)["C0001M01.XML"] is FileRole.SIDECAR
    # card-level files without a clip of their own ride along with the first clip
    assert roles(first)["MEDIAPRO.XML"] is FileRole.MEMBER
    assert set(second.paths) == {f"{card}/PRIVATE/M4ROOT/CLIP/C0002.MP4"}
    grouped = [p for g in result.groups for p in g.paths]
    assert sorted(grouped) == sorted(files)
    assert result.ungrouped == ()


def test_card_marker_directly_in_batch_uses_the_marker_as_root() -> None:
    files = [f"{B}/DCIM/100CANON/IMG_0001.CR3", f"{B}/DCIM/100CANON/IMG_0001.JPG"]
    (group,) = group_files(files, batch_root=B).groups
    assert group.card_structure_root == f"{B}/DCIM"
    assert group.media_type is MediaType.PHOTO


def test_other_card_layouts_are_recognised() -> None:
    for layout in ("AVCHD/BDMV/STREAM/00001.MTS", "XDROOT/Clip/C0001.MXF", "BDMV/STREAM/0.M2TS"):
        (group,) = group_files([f"{B}/card/{layout}"], batch_root=B).groups
        assert group.card_structure_root is not None, layout


def test_red_rdc_directory_is_one_clip() -> None:
    rdc = f"{B}/A001_C001_1012AB.RDC"
    files = [
        f"{rdc}/A001_C001_1012AB_001.R3D",
        f"{rdc}/A001_C001_1012AB_002.R3D",
        f"{rdc}/A001_C001_1012AB.RMD",
        f"{rdc}/A001_C001_1012AB_proxy.mov",
    ]
    (group,) = group_files(files, batch_root=B).groups
    assert group.card_structure_root == rdc
    assert group.primary.rel_path.endswith("_001.R3D")
    assert roles(group)["A001_C001_1012AB_002.R3D"] is FileRole.MEMBER
    assert roles(group)["A001_C001_1012AB_proxy.mov"] is FileRole.PROXY_CAMERA
    assert len(group.members) == 4


def test_cinema_dng_sequence_is_one_video_asset() -> None:
    folder = f"{B}/Clip_A"
    frames = [f"{folder}/A001_C002_{i:06d}.dng" for i in range(20)]
    (group,) = group_files(frames, batch_root=B).groups
    assert group.media_type is MediaType.VIDEO
    assert group.card_structure_root == folder
    assert group.primary.rel_path.endswith("_000000.dng")
    assert len(group.members) == 20


def test_short_runs_and_four_digit_photo_counters_are_not_sequences() -> None:
    photos = [f"{B}/IMG_{i:04d}.dng" for i in range(1, 30)]  # 4 digits: ordinary photo series
    short = [f"{B}/x/frame_{i:06d}.dng" for i in range(5)]  # too few frames
    result = group_files(photos + short, batch_root=B)
    assert len(result.groups) == 29 + 5


def test_sequence_with_a_gap_splits() -> None:
    frames = [f"{B}/s/f_{i:06d}.dpx" for i in [*range(0, 13), *range(20, 33)]]
    groups = group_files(frames, batch_root=B).groups
    assert [len(g.members) for g in groups] == [13, 13]


def test_loose_sequence_in_batch_folder_has_no_unit_root() -> None:
    frames = [f"{B}/f_{i:06d}.exr" for i in range(12)]
    (group,) = group_files(frames, batch_root=B).groups
    assert group.card_structure_root is None


def test_every_file_is_in_exactly_one_group_and_output_is_deterministic() -> None:
    files = [
        f"{B}/IMG_0001.CR3",
        f"{B}/IMG_0001.JPG",
        f"{B}/c/C0001.MP4",
        f"{B}/card/DCIM/100/IMG_1.JPG",
        f"{B}/card/DCIM/100/IMG_1.CR2",
        f"{B}/lonely.xmp",
        f"{B}/take.wav",
    ]
    result = group_files(files, batch_root=B)
    again = group_files(reversed(files), batch_root=B)
    assert result == again
    claimed = [p for g in result.groups for p in g.paths] + list(result.ungrouped)
    assert sorted(claimed) == sorted(files)
    assert len(set(claimed)) == len(claimed)


def test_loose_files_in_input_root() -> None:
    result = group_files(["Input/clip.mov", "Input/clip.xml"], batch_root="Input")
    (group,) = result.groups
    assert group.primary.rel_path == "Input/clip.mov"
    assert roles(group)["clip.xml"] is FileRole.SIDECAR

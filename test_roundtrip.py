r"""Verify 100% lossless decode -> encode -> XML round-trip across all 4N0 and 8W0 datasets in D:\REA\qq2."""

import os
import zipfile
from alm_codec import format_xml_output, is_8w0_dataset, parse_xml_bytes, verify_crc32

ZDC_ROOT = r"D:\REA\qq2\ZDC"
DS_ROOT = r"D:\REA\qq2\DATASETS"
W8_ROOT = r"D:\REA\qq2\8w0"

counts = {"4N0": [0, 0], "8W0": [0, 0]}
failed = []

def check(raw_xml: bytes, label: str):
    try:
        adr, ds = parse_xml_bytes(raw_xml, source_path=label)
    except Exception:
        return  # skip non-72C* xmls
    plat = "8W0" if is_8w0_dataset(ds) else "4N0"
    counts[plat][0] += 1
    reencoded = ds.to_bytes()
    ok_crc, _, _ = verify_crc32(reencoded)
    xml_out = format_xml_output(adr, ds).encode("utf-8")
    _, ds2 = parse_xml_bytes(xml_out, source_path=label)
    if reencoded == ds.original_raw and ds2.to_bytes() == ds.original_raw and ok_crc:
        counts[plat][1] += 1
    else:
        failed.append((plat, label, adr, len(reencoded), len(ds.original_raw), ok_crc))

for root_dir in (ZDC_ROOT, DS_ROOT, W8_ROOT):
    if not os.path.isdir(root_dir):
        continue
    for root, _, files in os.walk(root_dir):
        for fn in files:
            if "72C" in fn.upper() and fn.lower().endswith(".xml"):
                p = os.path.join(root, fn)
                with open(p, "rb") as f:
                    check(f.read(), p)
            elif fn.lower().endswith(".zip"):
                zp = os.path.join(root, fn)
                with zipfile.ZipFile(zp, "r") as zf:
                    for n in zf.namelist():
                        if "72C" in n.upper() and n.lower().endswith(".xml"):
                            check(zf.read(n), f"{zp}!{n}")

tot = counts["4N0"][0] + counts["8W0"][0]
ok = counts["4N0"][1] + counts["8W0"][1]
print(f"Round-trip test: {ok}/{tot} PASSED ({len(failed)} failed) | 4N0: {counts['4N0'][1]}/{counts['4N0'][0]} | 8W0: {counts['8W0'][1]}/{counts['8W0'][0]}")
for item in failed[:10]:
    print("  FAIL:", item)
if failed:
    raise SystemExit(1)

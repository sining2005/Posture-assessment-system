from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

from posture_assessment.import_export import (
    ExportOptions,
    UserExportService,
    UserImportService,
)
from posture_assessment.services import UserInput


def test_csv_import_with_incomplete_and_invalid_rows(db, user_service, settings, tmp_path: Path):
    source = tmp_path / "users.csv"
    source.write_text(
        "患者编号,姓名,出生日期,性别,手机\n"
        "P100,张三,1990-01-01,男,13800138000\n"
        "P101,李四,2026-13-41,女,13900139000\n",
        encoding="utf-8-sig",
    )
    service = UserImportService(db, user_service, settings.import_log_dir)
    preview = service.preview(source)
    result = service.import_file(source, preview.detected_mapping)
    assert result.success_count == 1
    assert result.error_count == 1
    assert result.error_log_path and result.error_log_path.exists()
    assert user_service.find_by_patient_no("P100").profile_complete is False


def test_duplicate_import_skip(db, user_service, settings, tmp_path: Path):
    user_service.create(
        UserInput(
            patient_no="P200",
            name="原用户",
            birth_date=date(1991, 1, 1),
            sex="男",
        ),
        require_complete=False,
    )
    source = tmp_path / "duplicate.csv"
    source.write_text(
        "患者编号,姓名,出生日期,性别\nP200,新名字,1991-01-01,男\n",
        encoding="utf-8-sig",
    )
    service = UserImportService(db, user_service, settings.import_log_dir)
    preview = service.preview(source)
    result = service.import_file(source, preview.detected_mapping, "skip")
    assert result.success_count == 0
    assert result.duplicate_count == 1
    assert user_service.find_by_patient_no("P200").name == "原用户"


def test_anonymized_zip_export(db, user_service, settings):
    user = user_service.create(
        UserInput(
            patient_no="EXPORT01",
            name="真实姓名",
            birth_date=date(1989, 2, 3),
            sex="女",
            mobile="13800138000",
            height_cm=165,
            weight_kg=55,
            address="真实地址",
        )
    )
    user_service.start_assessment(user.id)
    service = UserExportService(db, settings.export_dir)
    result = service.export(
        ExportOptions(
            user_ids=[user.id],
            modules=["体态检测"],
            anonymize=True,
            zip_package=True,
        )
    )
    assert result.output_path.exists()
    assert result.user_count == 1
    assert result.session_count == 1
    with zipfile.ZipFile(result.output_path) as archive:
        names = archive.namelist()
        assert "manifest.json" in names
        payload = json.loads(archive.read("原始结构.json").decode("utf-8"))
        exported = payload["users"][0]
        assert exported["姓名"] == ""
        assert exported["患者编号"].startswith("ANON-")


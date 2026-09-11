from __future__ import annotations

import difflib
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".vob",
    ".webm", ".wmv",
}


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None


def normalized_name(path: Path) -> str:
    """Normalize a filename for similarity comparison without losing useful words."""
    text = path.stem.casefold()
    for char in "_-.()[]{}":
        text = text.replace(char, " ")
    return " ".join(text.split())


def name_similarity(a: Path, b: Path) -> float:
    return difflib.SequenceMatcher(None, normalized_name(a), normalized_name(b)).ratio() * 100


def partial_hash(path: Path, sample_size: int = 1024 * 1024) -> str:
    """Hash the first and last sample_size bytes; useful as a cheap confirmation step."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        first = handle.read(sample_size)
        digest.update(first)
        if path.stat().st_size > sample_size:
            handle.seek(-sample_size, os.SEEK_END)
            digest.update(handle.read(sample_size))
    return digest.hexdigest()


def ffprobe_info(path: Path) -> VideoInfo:
    """Read video metadata using ffprobe. If ffprobe is unavailable, return file-only info."""
    base = VideoInfo(path=path, size=path.stat().st_size)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name,duration",
                "-of", "default=noprint_wrappers=1:nokey=0", str(path),
            ],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return base

    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()

    def integer(key: str) -> int | None:
        try:
            return int(values[key])
        except (KeyError, TypeError, ValueError):
            return None

    def number(key: str) -> float | None:
        try:
            return float(values[key])
        except (KeyError, TypeError, ValueError):
            return None

    return VideoInfo(
        path=path,
        size=base.size,
        duration=number("duration"),
        width=integer("width"),
        height=integer("height"),
        codec=values.get("codec_name"),
    )


def metadata_similarity(a: VideoInfo, b: VideoInfo) -> float:
    """Return a 0-100 similarity score for available video metadata."""
    scores: list[float] = []
    if a.size == b.size:
        scores.append(100)
    else:
        ratio = min(a.size, b.size) / max(a.size, b.size)
        scores.append(ratio * 100)

    if a.duration is not None and b.duration is not None:
        diff = abs(a.duration - b.duration)
        scores.append(max(0.0, 100.0 - min(diff / max(a.duration, b.duration, 1) * 100, 100)))
    if a.width and b.width and a.height and b.height:
        scores.append(100.0 if (a.width, a.height) == (b.width, b.height) else 50.0)
    if a.codec and b.codec:
        scores.append(100.0 if a.codec == b.codec else 50.0)
    return sum(scores) / len(scores)


def find_duplicate_groups(videos: list[VideoInfo], filename_threshold: float = 90.0) -> list[list[VideoInfo]]:
    """Find conservative duplicate candidates using size and/or filename similarity."""
    groups: list[list[VideoInfo]] = []
    used: set[Path] = set()

    # Same-size files are cheap to compare. Different-size files are considered only
    # when names are very similar, allowing resized/re-encoded copies to surface.
    for index, first in enumerate(videos):
        if first.path in used:
            continue
        group = [first]
        for second in videos[index + 1 :]:
            if second.path in used:
                continue
            similarity = name_similarity(first.path, second.path)
            same_size = first.size == second.size
            if similarity >= filename_threshold or (same_size and similarity >= 70.0):
                if metadata_similarity(first, second) >= 70.0:
                    group.append(second)
        if len(group) > 1:
            groups.append(group)
            used.update(item.path for item in group)
    return groups


class ScanWorker(QThread):
    progress = Signal(int, str)
    finished_scan = Signal(list)
    failed = Signal(str)

    def __init__(self, folders: list[Path], threshold: float) -> None:
        super().__init__()
        self.folders = folders
        self.threshold = threshold

    def run(self) -> None:
        try:
            paths: list[Path] = []
            seen: set[Path] = set()
            for folder in self.folders:
                for path in folder.rglob("*"):
                    if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS:
                        resolved = path.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            paths.append(resolved)

            total = max(len(paths), 1)
            videos: list[VideoInfo] = []
            for index, path in enumerate(paths, 1):
                videos.append(ffprobe_info(path))
                self.progress.emit(int(index * 100 / total), path.name)

            self.finished_scan.emit(find_duplicate_groups(videos, self.threshold))
        except Exception as exc:  # keep worker failures visible in the GUI
            self.failed.emit(f"검색 중 오류가 발생했습니다: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 중복 파일 찾기")
        self.resize(1050, 650)
        self.folders: list[Path] = []
        self.groups: list[list[VideoInfo]] = []
        self.worker: ScanWorker | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        controls = QHBoxLayout()
        self.add_button = QPushButton("폴더 추가")
        self.remove_button = QPushButton("폴더 제거")
        self.scan_button = QPushButton("중복 검색")
        self.recursive = QCheckBox("하위 폴더 포함")
        self.recursive.setChecked(True)
        self.threshold = QSpinBox()
        self.threshold.setRange(50, 100)
        self.threshold.setValue(90)
        self.threshold.setSuffix(" %")
        controls.addWidget(self.add_button)
        controls.addWidget(self.remove_button)
        controls.addWidget(self.recursive)
        controls.addWidget(QLabel("파일명 유사도"))
        controls.addWidget(self.threshold)
        controls.addStretch()
        controls.addWidget(self.scan_button)
        layout.addLayout(controls)

        self.folder_label = QLineEdit()
        self.folder_label.setReadOnly(True)
        self.folder_label.setPlaceholderText("검색할 폴더가 없습니다.")
        layout.addWidget(self.folder_label)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["선택", "중복 그룹", "파일명", "크기", "재생시간", "해상도", "경로"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        bottom = QHBoxLayout()
        self.status = QLabel("검색할 폴더를 추가하세요.")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.delete_button = QPushButton("선택 파일 삭제")
        self.open_button = QPushButton("탐색기에서 열기")
        bottom.addWidget(self.status)
        bottom.addWidget(self.progress)
        bottom.addStretch()
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.delete_button)
        layout.addLayout(bottom)

        self.add_button.clicked.connect(self.add_folder)
        self.remove_button.clicked.connect(self.remove_folder)
        self.scan_button.clicked.connect(self.start_scan)
        self.delete_button.clicked.connect(self.delete_selected)
        self.open_button.clicked.connect(self.open_selected)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "검색할 폴더 선택")
        if folder:
            path = Path(folder).resolve()
            if path not in self.folders:
                self.folders.append(path)
                self.folder_label.setText(" | ".join(str(item) for item in self.folders))
                self.status.setText(f"폴더 {len(self.folders)}개 선택됨")

    def remove_folder(self) -> None:
        if not self.folders:
            return
        self.folders.pop()
        self.folder_label.setText(" | ".join(str(item) for item in self.folders))
        self.status.setText(f"폴더 {len(self.folders)}개 선택됨")

    def start_scan(self) -> None:
        if not self.folders:
            QMessageBox.information(self, "폴더 필요", "먼저 검색할 폴더를 하나 이상 추가하세요.")
            return
        self.scan_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setText("영상 파일을 검색하는 중...")
        self.table.setRowCount(0)
        self.worker = ScanWorker(self.folders.copy(), float(self.threshold.value()))
        self.worker.progress.connect(lambda value, name: (self.progress.setValue(value), self.status.setText(f"메타정보 확인: {name}")))
        self.worker.finished_scan.connect(self.scan_finished)
        self.worker.failed.connect(self.scan_failed)
        self.worker.start()

    def scan_finished(self, groups: list[list[VideoInfo]]) -> None:
        self.groups = groups
        self.table.setRowCount(0)
        for group_index, group in enumerate(groups, 1):
            for info in group:
                row = self.table.rowCount()
                self.table.insertRow(row)
                check = QCheckBox()
                self.table.setCellWidget(row, 0, check)
                self.table.setItem(row, 1, QTableWidgetItem(str(group_index)))
                self.table.setItem(row, 2, QTableWidgetItem(info.path.name))
                self.table.setItem(row, 3, QTableWidgetItem(format_size(info.size)))
                self.table.setItem(row, 4, QTableWidgetItem(format_duration(info.duration)))
                resolution = f"{info.width}×{info.height}" if info.width and info.height else "-"
                self.table.setItem(row, 5, QTableWidgetItem(resolution))
                self.table.setItem(row, 6, QTableWidgetItem(str(info.path)))
                self.table.item(row, 2).setToolTip(str(info.path))
        self.scan_button.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(f"검색 완료: 중복 후보 {len(groups)}그룹 / {sum(len(g) for g in groups)}개 파일")

    def scan_failed(self, message: str) -> None:
        self.scan_button.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(message)
        QMessageBox.critical(self, "검색 오류", message)

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if isinstance(widget, QCheckBox) and widget.isChecked():
                paths.append(Path(self.table.item(row, 6).text()))
        return paths

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 없음", "삭제할 파일을 선택하세요.")
            return
        answer = QMessageBox.warning(
            self, "파일 삭제 확인",
            f"선택한 {len(paths)}개 파일을 휴지통으로 보내지 않고 영구 삭제합니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for path in paths:
            try:
                path.unlink()
            except OSError as exc:
                QMessageBox.warning(self, "삭제 실패", f"{path}\n{exc}")
        self.start_scan()

    def open_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 없음", "탐색기에서 열 파일을 선택하세요.")
            return
        path = paths[0]
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

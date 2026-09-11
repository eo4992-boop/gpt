from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from send2trash import send2trash

VIDEO_EXTENSIONS = {
    ".3g2", ".3gp", ".asf", ".avi", ".divx", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".vob",
    ".webm", ".wmv",
}


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    size: int


def normalized_name(path: Path) -> str:
    text = path.stem.casefold()
    for char in "_-.()[]{}":
        text = text.replace(char, " ")
    return " ".join(text.split())


def name_similarity(a: Path, b: Path) -> float:
    return difflib.SequenceMatcher(None, normalized_name(a), normalized_name(b)).ratio() * 100


def candidate_reason(a: VideoInfo, b: VideoInfo, threshold: float) -> str | None:
    similarity = name_similarity(a.path, b.path)
    if similarity < threshold:
        return None
    size_text = "동일" if a.size == b.size else "다름"
    return f"파일 크기 {size_text} + 파일명 {similarity:.0f}%"


def find_duplicate_groups(videos: list[VideoInfo], filename_threshold: float = 90.0) -> list[list[VideoInfo]]:
    groups: list[list[VideoInfo]] = []
    used: set[Path] = set()
    for index, first in enumerate(videos):
        if first.path in used:
            continue
        group = [first]
        for second in videos[index + 1:]:
            if second.path not in used and candidate_reason(first, second, filename_threshold):
                group.append(second)
        if len(group) > 1:
            groups.append(group)
            used.update(item.path for item in group)
    return groups


class ScanWorker(QThread):
    progress = Signal(int, str)
    finished_scan = Signal(list)
    failed = Signal(str)

    def __init__(self, folders: list[Path], threshold: float, recursive: bool) -> None:
        super().__init__()
        self.folders, self.threshold, self.recursive = folders, threshold, recursive

    def run(self) -> None:
        try:
            paths: list[Path] = []
            seen: set[Path] = set()
            for folder in self.folders:
                iterator = folder.rglob("*") if self.recursive else folder.glob("*")
                for path in iterator:
                    if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS:
                        resolved = path.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            paths.append(resolved)
            total = max(len(paths), 1)
            videos: list[VideoInfo] = []
            for index, path in enumerate(paths, 1):
                videos.append(VideoInfo(path, path.stat().st_size))
                self.progress.emit(int(index * 100 / total), path.name)
            self.finished_scan.emit(find_duplicate_groups(videos, self.threshold))
        except Exception as exc:
            self.failed.emit(f"검색 중 오류가 발생했습니다: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 중복 파일 찾기")
        self.resize(1120, 650)
        self.folders: list[Path] = []
        self.groups: list[list[VideoInfo]] = []
        self.worker: ScanWorker | None = None

        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        controls = QHBoxLayout()
        self.add_button = QPushButton("폴더 추가")
        self.remove_button = QPushButton("마지막 폴더 제거")
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

        self.folder_label = QLabel("검색할 폴더가 없습니다.")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["선택", "그룹", "파일명", "크기", "판정", "경로"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        bottom = QHBoxLayout()
        self.status = QLabel("검색할 폴더를 추가하세요.")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.delete_button = QPushButton("선택 파일 휴지통으로 이동")
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

    def update_folder_label(self) -> None:
        if self.folders:
            self.folder_label.setText(" | ".join(str(item) for item in self.folders))
        else:
            self.folder_label.setText("검색할 폴더가 없습니다.")

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "검색할 폴더 선택")
        if folder:
            path = Path(folder).resolve()
            if path not in self.folders:
                self.folders.append(path)
                self.update_folder_label()
                self.status.setText(f"폴더 {len(self.folders)}개 선택됨")

    def remove_folder(self) -> None:
        if self.folders:
            self.folders.pop()
            self.update_folder_label()
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
        self.worker = ScanWorker(
            self.folders.copy(), float(self.threshold.value()), self.recursive.isChecked()
        )
        self.worker.progress.connect(
            lambda value, name: (
                self.progress.setValue(value), self.status.setText(f"파일 확인: {name}")
            )
        )
        self.worker.finished_scan.connect(self.scan_finished)
        self.worker.failed.connect(self.scan_failed)
        self.worker.start()

    def scan_finished(self, groups: list[list[VideoInfo]]) -> None:
        self.groups = groups
        self.table.setRowCount(0)
        for group_index, group in enumerate(groups, 1):
            first = group[0]
            for info in group:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setCellWidget(row, 0, QCheckBox())
                self.table.setItem(row, 1, QTableWidgetItem(str(group_index)))
                self.table.setItem(row, 2, QTableWidgetItem(info.path.name))
                self.table.setItem(row, 3, QTableWidgetItem(format_size(info.size)))
                reason = candidate_reason(first, info, float(self.threshold.value())) if info is not first else "기준 파일"
                self.table.setItem(row, 4, QTableWidgetItem(reason or "중복 후보"))
                self.table.setItem(row, 5, QTableWidgetItem(str(info.path)))
                self.table.item(row, 2).setToolTip(str(info.path))
        self.scan_button.setEnabled(True)
        self.progress.setVisible(False)
        count = sum(len(group) for group in groups)
        self.status.setText(f"검색 완료: 중복 후보 {len(groups)}그룹 / {count}개 파일")

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
                paths.append(Path(self.table.item(row, 5).text()))
        return paths

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 없음", "삭제할 파일을 선택하세요.")
            return
        answer = QMessageBox.warning(
            self,
            "파일 삭제 확인",
            f"선택한 {len(paths)}개 파일을 Windows 휴지통으로 이동합니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        failures: list[str] = []
        for path in paths:
            try:
                send2trash(str(path))
            except OSError as exc:
                failures.append(f"{path}\n{exc}")
        if failures:
            QMessageBox.warning(self, "일부 삭제 실패", "\n\n".join(failures[:5]))
        self.start_scan()

    def open_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 없음", "탐색기에서 열 파일을 선택하세요.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0].parent)))


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

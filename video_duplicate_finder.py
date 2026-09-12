from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
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
from rapidfuzz import fuzz, process
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
    text = re.sub(r"\s*\(\d+\)\s*$", "", text)
    for char in "_-.()[]{}":
        text = text.replace(char, " ")
    return " ".join(text.split())


def name_similarity(a: Path, b: Path) -> float:
    return fuzz.ratio(normalized_name(a), normalized_name(b))


def _name_is_candidate(a: Path, b: Path, threshold: float) -> bool:
    left = normalized_name(a)
    right = normalized_name(b)
    if left == right:
        return True
    return fuzz.ratio(left, right, score_cutoff=threshold) >= threshold


def candidate_reason(a: VideoInfo, b: VideoInfo, threshold: float) -> str | None:
    if not _name_is_candidate(a.path, b.path, threshold):
        return None
    similarity = name_similarity(a.path, b.path)
    if a.size == b.size:
        return f"강한 후보: 크기 동일 + 파일명 {similarity:.0f}%"
    return f"주의 후보: 크기 다름 + 파일명 {similarity:.0f}%"


def find_duplicate_groups(
    videos: list[VideoInfo],
    filename_threshold: float = 80.0,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[list[VideoInfo]]:
    """Build the candidate graph with the native RapidFuzz matcher.

    Each pair is evaluated only once (against later items), while RapidFuzz
    performs the expensive string work in optimized native code and can reject
    below-threshold matches early. The graph semantics remain unchanged.
    """
    count = len(videos)
    adjacency: list[set[int]] = [set() for _ in videos]
    names = [normalized_name(video.path) for video in videos]

    for index in range(count):
        if index + 1 < count:
            query = names[index]
            choices = names[index + 1:]
            for _, score, local_index in process.extract_iter(
                query,
                choices,
                scorer=fuzz.ratio,
                score_cutoff=filename_threshold,
                score_hint=filename_threshold,
            ):
                second_index = index + 1 + local_index
                if score >= filename_threshold:
                    adjacency[index].add(second_index)
                    adjacency[second_index].add(index)
        if progress_callback is not None:
            progress_callback(index + 1, count)

    groups: list[list[VideoInfo]] = []
    visited: set[int] = set()
    for start in range(count):
        if start in visited or not adjacency[start]:
            continue
        stack = [start]
        visited.add(start)
        indexes: list[int] = []
        while stack:
            current = stack.pop()
            indexes.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        indexes.sort(key=lambda item: (-videos[item].size, str(videos[item].path).casefold()))
        groups.append([videos[item] for item in indexes])
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
            discovered = 0

            for folder in self.folders:
                if self.recursive:
                    walker = os.walk(folder)
                    for root, _, filenames in walker:
                        for filename in filenames:
                            path = Path(root) / filename
                            if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                                continue
                            resolved = path.resolve()
                            if resolved not in seen:
                                seen.add(resolved)
                                paths.append(resolved)
                                discovered += 1
                                if discovered == 1 or discovered % 25 == 0:
                                    self.progress.emit(0, f"영상 파일 발견: {discovered}개")
                else:
                    for entry in os.scandir(folder):
                        if not entry.is_file():
                            continue
                        path = Path(entry.path)
                        if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                            continue
                        resolved = path.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            paths.append(resolved)
                            discovered += 1
                            if discovered == 1 or discovered % 25 == 0:
                                self.progress.emit(0, f"영상 파일 발견: {discovered}개")

            total = len(paths)
            if not total:
                self.progress.emit(100, "동영상 파일이 없습니다.")
                self.finished_scan.emit([])
                return

            videos: list[VideoInfo] = []
            for index, path in enumerate(paths, 1):
                videos.append(VideoInfo(path, path.stat().st_size))
                self.progress.emit(
                    int(index * 35 / total),
                    f"파일 정보 확인: {index}/{total} - {path.name}",
                )

            def report_compare(index: int, compare_total: int) -> None:
                value = 35 + int(index * 65 / max(compare_total, 1))
                self.progress.emit(value, f"중복 후보 분석: {index}/{compare_total}")

            groups = find_duplicate_groups(
                videos,
                self.threshold,
                progress_callback=report_compare,
            )
            self.finished_scan.emit(groups)
        except Exception as exc:
            self.failed.emit(f"검색 중 오류가 발생했습니다: {exc}")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("영상 중복 파일 찾기")
        self.resize(1200, 680)
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
        self.threshold.setValue(80)
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

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["삭제", "그룹", "파일명", "크기", "판정", "보존 권장", "경로"]
        )
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
        if self.worker is not None and self.worker.isRunning():
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
            lambda value, message: (
                self.progress.setValue(value), self.status.setText(message)
            )
        )
        self.worker.finished_scan.connect(self.scan_finished)
        self.worker.failed.connect(self.scan_failed)
        self.worker.start()

    def scan_finished(self, groups: list[list[VideoInfo]]) -> None:
        self.groups = groups
        self.table.setRowCount(0)
        for group_index, group in enumerate(groups, 1):
            keeper = group[0]
            for info in group:
                row = self.table.rowCount()
                self.table.insertRow(row)
                delete_box = QCheckBox()
                self.table.setCellWidget(row, 0, delete_box)
                self.table.setItem(row, 1, QTableWidgetItem(str(group_index)))
                self.table.setItem(row, 2, QTableWidgetItem(info.path.name))
                self.table.setItem(row, 3, QTableWidgetItem(format_size(info.size)))
                reason = (
                    "기준 파일"
                    if info is keeper
                    else candidate_reason(keeper, info, float(self.threshold.value())) or "후보"
                )
                self.table.setItem(row, 4, QTableWidgetItem(reason))
                keep_text = "보존 권장 (가장 큰 파일)" if info is keeper else "삭제 검토"
                self.table.setItem(row, 5, QTableWidgetItem(keep_text))
                self.table.setItem(row, 6, QTableWidgetItem(str(info.path)))
                self.table.item(row, 2).setToolTip(str(info.path))
                self.table.item(row, 6).setToolTip(str(info.path))
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
                paths.append(Path(self.table.item(row, 6).text()))
        return paths

    def _keeper_paths(self) -> set[Path]:
        return {group[0].path for group in self.groups if group}

    def _groups_without_keeper(self, selected_paths: list[Path]) -> list[list[VideoInfo]]:
        selected = set(selected_paths)
        return [group for group in self.groups if group and all(info.path in selected for info in group)]

    def delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 없음", "삭제할 파일을 선택하세요.")
            return

        empty_groups = self._groups_without_keeper(paths)
        if empty_groups:
            QMessageBox.warning(
                self,
                "삭제 중단",
                "후보 그룹의 모든 파일을 삭제하도록 선택했습니다.\n"
                "각 그룹에서 최소 1개 파일은 남겨야 합니다.\n\n"
                "삭제 체크를 조정한 뒤 다시 시도하세요.",
            )
            return

        keeper_paths = self._keeper_paths()
        selected_keepers = [path for path in paths if path in keeper_paths]
        lines = [str(path) for path in paths]
        message = (
            f"선택한 {len(paths)}개 파일을 Windows 휴지통으로 이동합니다.\n\n"
            + "\n".join(lines[:20])
            + ("\n..." if len(lines) > 20 else "")
        )
        if selected_keepers:
            message += (
                "\n\n⚠ 보존 권장 파일이 포함되어 있습니다.\n"
                "보존 권장 표시는 가장 큰 파일이라는 참고 정보일 뿐이며,"
                " 삭제하면 안 되는 원본이라는 뜻은 아닙니다."
            )
        answer = QMessageBox.warning(
            self,
            "파일 삭제 최종 확인",
            message + "\n\n계속하시겠습니까?",
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
        if self.folders:
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

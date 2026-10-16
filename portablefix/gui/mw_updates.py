"""App self-update check, download and hand-off."""

import os
import shutil
import sys
import tempfile

from PySide6.QtWidgets import QMessageBox
from pathlib import Path

from .. import elevation, paths, update_swap, updater
from ..version import APP_VERSION
from .mw_common import _UPDATE_PHASE_KEYS, _thread_running


class UpdatesMixin:
    """App self-update check, download and hand-off.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _start_update_check(self, manual: bool = False) -> None:
        if not getattr(sys, "frozen", False):
            # From source there is no release to compare against - say so
            # on an explicit click instead of silently doing nothing.
            if manual:
                self.statusBar().showMessage(self._t("update_check_dev_build"), 8000)
            return
        # Whatever starts a check now (a click, leaving quiet mode) covers
        # the one skipped at start.
        self._startup_update_check_skipped = False
        if manual:
            self._manual_update_check = True
            self.statusBar().showMessage(self._t("update_check_running"), 8000)
        # A click while the start-up check is still out just waits for its
        # answer (now reported, since the flag is set) instead of asking twice.
        if _thread_running(self._update_check_runner):
            return
        self._update_check_runner = updater.UpdateCheckRunner(APP_VERSION, parent=self)
        self._update_check_runner.check_finished.connect(self._on_update_check_finished)
        self._update_check_runner.start()

    def _on_update_check_finished(self, info) -> None:
        manual = self._manual_update_check
        self._manual_update_check = False
        if self._closed:
            return
        # Only an explicit click answers "nothing new": the automatic check
        # at start has always stayed silent.
        if info is None and manual:
            self.statusBar().showMessage(self._t("update_check_none"), 8000)
        # A local update started with the developer switch may already be
        # running when the release check comes back.
        if info is None or self._update_in_progress:
            return
        self._pending_update_info = info
        self.update_banner_label.setText(self._t("update_available_banner").format(version=info.version))
        self.update_banner.setVisible(True)

    def _on_update_dismiss_clicked(self) -> None:
        self._pending_update_info = None
        self.update_banner.setVisible(False)

    def _quit_app(self) -> None:
        # Route through close() (not QApplication.quit() directly) so
        # closeEvent's cancel/wait cleanup for every in-flight runner always
        # runs first - quit() bypasses closeEvent entirely.
        self.close()

    def _on_update_button_clicked(self) -> None:
        if self._batch_active or self._update_in_progress:
            return
        info = self._pending_update_info
        if info is None:
            return
        if self._reusable_stage() is not None:
            # Downloaded and verified already (the last hand-off failed or
            # was refused) - straight to the restart question.
            self._confirm_and_launch_update()
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_download").format(version=info.version),
        )
        if confirmed != QMessageBox.Yes:
            return
        self._start_update_download(info)

    def start_local_update(self, zip_path: Path, sha256: str) -> None:
        """Developer switch (--update-from-zip with PORTABLEFIX_DEV_UPDATE=1,
        see main.py): feeds a local release zip through the same verify,
        stage, confirm, hand-off and close steps as a downloaded one, so the
        real updater can be tested before a release is published."""
        if self._batch_active or self._update_in_progress:
            return
        zip_path = Path(zip_path)
        info = updater.UpdateInfo(version=f"{zip_path.name} (dev)", package_url=str(zip_path), sha256_url=None, notes="")
        self._pending_update_info = info
        self._staged_update = None
        self.update_banner.setVisible(True)
        self._start_update_download(info, local_zip=zip_path, local_sha256=sha256)

    def _begin_update_step(self, phase: str) -> None:
        self._update_in_progress = True
        self._update_phase = phase
        self._update_progress = (0, 0)
        self.update_banner_label.setText(self._t(_UPDATE_PHASE_KEYS[phase]))
        self.update_banner_label.setToolTip("")
        self.update_button.setEnabled(False)
        self.update_dismiss_button.setEnabled(False)
        self.progress_bar.setFormat("%p%")  # bytes, not an action count
        self.progress_bar.setMaximum(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

    def _end_update_step(self) -> None:
        self._update_in_progress = False
        self._update_phase = None
        self.progress_bar.setVisible(False)
        self.update_button.setEnabled(True)
        self.update_dismiss_button.setEnabled(True)
        # A language toggle mid-update rebuilt the dashboard with Analyze
        # locked (_build_dashboard_card) - nothing else would unlock it.
        self.dashboard_analyze_button.setEnabled(not self._batch_start_blocked())

    def _discard_update_download(self) -> None:
        # The zip is gone once staged; the folder (or a zip that failed to
        # download, verify or stage) would otherwise pile up in %TEMP%.
        if self._update_download_dir is not None:
            shutil.rmtree(self._update_download_dir, ignore_errors=True)
            self._update_download_dir = None

    def _reusable_stage(self):
        staged = self._staged_update
        info = self._pending_update_info
        if staged is None or info is None or staged.version != info.version:
            return None
        if not (staged.stage_root / "App" / "PortableFix.exe").is_file():
            # Removed meanwhile (by the updater, or by hand) - stage again.
            self._staged_update = None
            return None
        return staged

    def _start_update_download(self, info, local_zip: Path | None = None, local_sha256: str = "") -> None:
        try:
            dest_dir = Path(tempfile.mkdtemp(prefix="PortableFixUpdate_"))
        except OSError as exc:
            self.update_banner_label.setText(self._t("update_download_failed"))
            self.update_banner_label.setToolTip(str(exc))
            return
        self._discard_update_download()
        self._update_download_dir = dest_dir
        self._begin_update_step("download")
        runner = updater.UpdateDownloadRunner(info, dest_dir, parent=self, local_zip=local_zip, local_sha256=local_sha256)
        self._update_download_runner = runner
        runner.download_finished.connect(self._on_update_download_finished)
        runner.progress.connect(self._on_update_download_progress)
        runner.start()

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        # Staging reports through here too. A late signal from a step that
        # already ended must not bring the hidden bar back.
        if not self._update_in_progress:
            return
        self._update_progress = (downloaded, total) if total > 0 else (0, 0)
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)
        else:
            # Server didn't send a usable Content-Length - indeterminate
            # (busy/marquee) beats a progress bar frozen at 0%.
            self.progress_bar.setMaximum(0)

    def _on_update_download_finished(self, zip_path, error: str) -> None:
        if self._closed:
            return
        info = self._pending_update_info
        if not zip_path or info is None:
            self._end_update_step()
            self._discard_update_download()
            self.update_banner_label.setText(self._t("update_download_failed"))
            # The technician's "why" (HTTP error, hash mismatch) without
            # pushing a raw exception text into the banner itself.
            self.update_banner_label.setToolTip(error)
            return
        install_dir = paths.get_base_dir()
        if not updater.is_writable(install_dir):
            self._end_update_step()
            self._discard_update_download()
            key = "update_needs_admin" if updater.needs_elevation_for_update(install_dir) else "update_not_writable"
            self.update_banner_label.setText(self._t(key))
            return
        self._staged_update = None
        self._begin_update_step("stage")
        runner = updater.UpdateStageRunner(Path(zip_path), install_dir, info.version, parent=self)
        self._update_stage_runner = runner
        runner.stage_finished.connect(self._on_update_stage_finished)
        runner.progress.connect(self._on_update_download_progress)
        runner.start()

    def _on_update_stage_finished(self, staged, error: str) -> None:
        if self._closed:
            return
        self._discard_update_download()
        if staged is None:
            self._end_update_step()
            self.update_banner_label.setText(self._t("update_stage_failed"))
            self.update_banner_label.setToolTip(error)
            QMessageBox.warning(
                self, self._t("app_title"),
                self._t("update_stage_failed_detail").format(detail=error, url=updater.RELEASES_PAGE_URL),
            )
            return
        self._staged_update = staged
        self._confirm_and_launch_update()

    def _long_running_tasks(self) -> list[str]:
        """What would keep this process alive long after close() - the
        updater waits for it to exit, and a close that takes minutes (winget
        alone allows 5) would outlast that wait. Named for the refusal."""
        tasks = []
        if self._batch_active:
            tasks.append(self._t("update_busy_batch"))
        if self._report_runner is not None:
            tasks.append(self._t("update_busy_report"))
        if self._speed_test_busy or _thread_running(self._speed_test_runner):
            tasks.append(self._t("update_busy_speed_test"))
        # Not the winget scan: it only reads, starts on its own with the
        # window (so it ran on every early "Update" click), and closeEvent
        # waits at most 65 s for it - well inside the updater's 600 s.
        if _thread_running(self._winget_update_runner):
            tasks.append(self._t("update_busy_winget_update"))
        if _thread_running(self._pending_restore_point_runner) or _thread_running(self._pending_panel_restore_point_runner):
            tasks.append(self._t("update_busy_restore_point"))
        if _thread_running(self._uninstall_runner):
            tasks.append(self._t("update_busy_uninstall"))
        return tasks

    def _show_update_available(self) -> None:
        info = self._pending_update_info
        if info is not None:
            self.update_banner_label.setText(self._t("update_available_banner").format(version=info.version))

    def _confirm_and_launch_update(self) -> None:
        self._end_update_step()
        info = self._pending_update_info
        staged = self._staged_update
        if info is None or staged is None:
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_restart").format(version=info.version),
        )
        if confirmed != QMessageBox.Yes or self._closed:
            self._show_update_available()
            return
        # Checked after the question: its nested event loop keeps timers
        # (the winget auto-check) running.
        busy = self._long_running_tasks()
        if busy:
            self._show_update_available()
            QMessageBox.warning(
                self, self._t("app_title"),
                self._t("update_busy_tasks").format(tasks="\n".join(f"• {task}" for task in busy)),
            )
            return
        self._begin_update_step("launch")
        runner = updater.UpdateLaunchRunner(staged, paths.get_base_dir(), parent=self)
        self._update_launch_runner = runner
        runner.launch_finished.connect(self._on_update_launch_finished)
        runner.start()

    def _on_update_launch_finished(self, result) -> None:
        if self._closed:
            return
        if result.ok:
            # The updater holds this process's handle and starts replacing
            # App\ the moment it exits - no questions, no lingering.
            self._closing_for_update = True
            self._quit_app()
            return
        self._end_update_step()
        self.update_banner_label.setText(self._t("update_apply_failed"))
        if result.reason == updater.REASON_CANCELLED:
            return
        detail = (result.detail or "").strip()
        if len(detail) > 800:
            # The full launch-log tail is in the diagnostics file.
            detail = "..." + detail[-800:]
        log_dir = result.log_dir or updater.update_log_dir() or "%TEMP%\\PortableFixUpdate"
        QMessageBox.warning(
            self, self._t("app_title"),
            self._t("update_launch_failed_detail").format(
                reason=self._t(f"update_reason_{result.reason}"),
                detail=detail,
                log_dir=log_dir,
                url=updater.RELEASES_PAGE_URL,
            ),
        )

    def _on_restart_as_admin(self) -> None:
        # UAC would fire first and the close confirmation only afterwards.
        if self._long_running_tasks():
            self.statusBar().showMessage(self._t("restart_admin_busy"))
            return
        # In a frozen build sys.executable IS the app - no args needed. In
        # dev mode it's python.exe, which needs the script path re-passed or
        # elevating just opens a bare interpreter instead of restarting the app.
        args = [] if getattr(sys, "frozen", False) else list(sys.argv)
        # The elevated copy waits for this process (and the onefile
        # bootloader, which still maps the exe) to exit before it takes the
        # single-instance mutex - otherwise it lost that race and quit.
        wait_pids = [os.getpid()]
        parent_pid = update_swap.onefile_parent_pid()
        if parent_pid:
            wait_pids.append(parent_pid)
        result = elevation.relaunch_as_admin(sys.executable, args, wait_pids=wait_pids)
        if result <= 32:
            QMessageBox.warning(
                self,
                self._t("app_title"),
                self._t("elevation_failed"),
            )
        else:
            self.close()

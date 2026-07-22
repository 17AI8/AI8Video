from __future__ import annotations

import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.media import local_tts


class AI8VideoLocalTtsTest(unittest.TestCase):
    def test_ensure_local_tts_dir_migrates_legacy_folder_name(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            user_root = Path(tempdir) / "用户文件夹"
            user_root_backup = local_tts.USER_FILE_ROOT
            legacy_root = Path(tempdir) / "用户文件夹" / "本地TTS"
            legacy_root.mkdir(parents=True, exist_ok=True)
            (legacy_root / "settings.json").write_text('{"engine":"sherpa-onnx","voice":"旧音色"}', encoding="utf-8")
            try:
                os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                local_tts.USER_FILE_ROOT = user_root
                with patch.object(local_tts, "ensure_user_file_root", return_value=user_root):
                    root = local_tts.ensure_local_tts_dir()
            finally:
                local_tts.USER_FILE_ROOT = user_root_backup
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

            self.assertEqual(root.name, "TTS")
            self.assertTrue((root / "settings.json").is_file())
            self.assertFalse(legacy_root.exists())

    def test_local_tts_status_normalizes_legacy_engine_to_mimo_api(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            tts_root = Path(tempdir) / "tts"
            tts_root.mkdir(parents=True, exist_ok=True)
            (tts_root / "settings.json").write_text(
                '{"engine":"sherpa-onnx","voice":"旧音色","apiKey":"mimo-test-key"}',
                encoding="utf-8",
            )
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(tts_root)
                status = local_tts.local_tts_status()
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["engine"], "mimo-api")
        self.assertEqual(status["voice"], "旧音色")
        self.assertEqual(status["voiceCount"], len(local_tts.MIMO_API_PRESET_VOICE_OPTIONS))
        self.assertEqual(status["voiceLabel"], "旧音色")
        self.assertTrue(status["available"])

    def test_local_tts_status_defaults_to_mimo_api(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
                status = local_tts.local_tts_status()
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["engine"], "mimo-api")
        self.assertEqual(status["voice"], "冰糖")
        self.assertEqual(status["model"], "mimo-v2.5-tts")
        self.assertEqual(status["apiBaseUrl"], "https://api.xiaomimimo.com/v1")
        self.assertEqual(status["voiceCount"], len(local_tts.MIMO_API_PRESET_VOICE_OPTIONS))
        self.assertEqual(status["voiceOptions"][0]["value"], "mimo_default")
        self.assertEqual(status["voiceOptions"][1]["value"], "冰糖")
        self.assertFalse(status["available"])
        self.assertIn("MiMo API Key", status["availabilityReason"])

    def test_update_local_tts_settings_persists_mimo_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
                status = local_tts.update_local_tts_settings({
                    "engine": "mimo-api",
                    "apiBaseUrl": "https://api.xiaomimimo.com/v1/",
                    "apiKey": "mimo-test-key",
                    "model": "mimo-v2.5-tts",
                    "voice": "冰糖",
                })
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["engine"], "mimo-api")
        self.assertEqual(status["apiBaseUrl"], "https://api.xiaomimimo.com/v1")
        self.assertEqual(status["apiKey"], "mimo-test-key")
        self.assertEqual(status["model"], "mimo-v2.5-tts")
        self.assertNotIn("stylePrompt", status)
        self.assertNotIn("audioTag", status)
        self.assertEqual(status["voice"], "冰糖")
        self.assertEqual(status["voiceCount"], len(local_tts.MIMO_API_PRESET_VOICE_OPTIONS))
        self.assertTrue(status["available"])
        self.assertEqual(status["modelDir"], "")

    def test_voice_options_for_mimo_api_match_official_preset_list(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
                options = local_tts._voice_options_for_engine("mimo-api", local_tts.default_sherpa_onnx_model_dir())
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual([item["value"] for item in options], [
            "mimo_default",
            "冰糖",
            "茉莉",
            "苏打",
            "白桦",
            "Mia",
            "Chloe",
            "Milo",
            "Dean",
        ])

    def test_local_tts_status_appends_uploaded_voice_clone_items_to_dropdown(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                tts_root = Path(tempdir) / "tts"
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(tts_root)
                clone_dir = tts_root / "音色克隆"
                clone_dir.mkdir(parents=True, exist_ok=True)
                (clone_dir / "主播样本.mp3").write_bytes(b"mp3-data")
                status = local_tts.local_tts_status()
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["voiceCloneCount"], 1)
        self.assertEqual(status["voiceCount"], len(local_tts.MIMO_API_PRESET_VOICE_OPTIONS) + 1)
        self.assertEqual(status["voiceOptions"][-1]["value"], "clone:主播样本.mp3")
        self.assertEqual(status["voiceOptions"][-1]["label"], "克隆 · 主播样本")

    def test_local_tts_status_migrates_legacy_voice_clone_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                tts_root = Path(tempdir) / "tts"
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(tts_root)
                legacy_dir = tts_root / "音色复刻"
                legacy_dir.mkdir(parents=True, exist_ok=True)
                (legacy_dir / "旧样本.wav").write_bytes(b"wav-data")
                status = local_tts.local_tts_status()
                migrated = tts_root / "音色克隆" / "旧样本.wav"
                migrated_exists = migrated.is_file()
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertTrue(migrated_exists)
        self.assertEqual(status["voiceOptions"][-1]["value"], "clone:旧样本.wav")
        self.assertEqual(status["voiceOptions"][-1]["label"], "克隆 · 旧样本")

    def test_synthesize_local_tts_calls_mimo_voiceclone_with_uploaded_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            output = Path(tempdir) / "preview.m4a"
            try:
                tts_root = Path(tempdir) / "tts"
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(tts_root)
                clone_dir = tts_root / "音色克隆"
                clone_dir.mkdir(parents=True, exist_ok=True)
                sample = clone_dir / "主播样本.mp3"
                sample.write_bytes(b"sample-mp3")

                class FakeResponse:
                    ok = True
                    status_code = 200
                    reason = "OK"

                    def json(self):
                        return {
                            "choices": [
                                {
                                    "message": {
                                        "audio": {
                                            "data": base64.b64encode(b"wav-bytes").decode("ascii"),
                                        }
                                    }
                                }
                            ]
                        }

                def fake_convert(_source, target_path, *, ffmpeg_bin=None, volume_multiplier=None):
                    Path(target_path).write_bytes(b"m4a")

                with patch.object(local_tts.requests, "post", return_value=FakeResponse()) as post, \
                        patch.object(local_tts, "_convert_audio_to_m4a", side_effect=fake_convert):
                    result = local_tts.synthesize_local_tts(
                        "试听文本",
                        output,
                        settings={
                            "engine": "mimo-api",
                            "apiBaseUrl": "https://api.xiaomimimo.com/v1",
                            "apiKey": "mimo-test-key",
                            "cloneModel": "mimo-v2.5-tts-voiceclone",
                            "voice": "clone:主播样本.mp3",
                            "rate": 185,
                        },
                        output_volume=1.0,
                    )
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(result["status"], "generated")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "mimo-v2.5-tts-voiceclone")
        self.assertTrue(post.call_args.kwargs["json"]["audio"]["voice"].startswith("data:audio/mpeg;base64,"))

    def test_save_local_tts_voice_clone_upload_selects_prepared_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")

                class FakeUpload:
                    filename = "样本视频.mp4"

                    def save(self, target: str, overwrite: bool = False) -> None:
                        Path(target).write_bytes(b"video")

                def fake_prepare(_source, target, *, ffmpeg_bin=None):
                    Path(target).write_bytes(b"prepared-wav")

                with patch.object(local_tts, "_prepare_voice_clone_sample", side_effect=fake_prepare):
                    status = local_tts.save_local_tts_voice_clone_upload(FakeUpload())
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["voice"], "clone:样本视频.wav")
        self.assertEqual(status["voiceCloneCount"], 1)
        self.assertEqual(status["voiceCloneItems"][0]["name"], "样本视频.wav")

    def test_prepare_voice_clone_sample_exports_high_quality_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source.mp4"
            target = Path(tempdir) / "target.wav"
            source.write_bytes(b"video")
            with patch("subprocess.run") as run:
                local_tts._prepare_voice_clone_sample(source, target, ffmpeg_bin="/usr/bin/ffmpeg")

        args = run.call_args.args[0]
        self.assertIn("-ar", args)
        self.assertEqual(args[args.index("-ar") + 1], "48000")
        self.assertIn("-af", args)
        self.assertEqual(args[args.index("-af") + 1], local_tts.LOCAL_TTS_CLONE_AUDIO_FILTER)
        self.assertIn("pcm_s16le", args)

    def test_prepare_narration_text_prefers_dialogue_fields(self) -> None:
        prompt = (
            "镜头一（0-5s）：中景，人物看向镜头。台词/口播：“今天正式开始。”情绪语气：坚定。\n"
            "镜头二（5-10s）：近景，人物抬手示意。旁白：跨境沟通终于不用卡在语言上。音效：轻微提示音。\n"
            "所有主体最后一秒尽可能全身正对着镜头。"
        )

        text = local_tts.prepare_narration_text(prompt)

        self.assertEqual(text, "今天正式开始。 跨境沟通终于不用卡在语言上")
        self.assertNotIn("中景", text)
        self.assertNotIn("情绪语气", text)
        self.assertNotIn("最后一秒", text)

    def test_prepare_narration_text_supports_parenthesized_voiceover_labels(self) -> None:
        prompt = (
            "镜头一（0-5s）：中近景，女性角色在明亮办公室。"
            "台词（画外音，好奇且惊喜）："
            "“刚刚体验完，我真的被震惊了。它不仅是聊天软件，更是AI8video 超级平台。”"
            "音效：清脆的鼠标点击声。\n"
            "镜头二（5-10s）：特写。"
            "台词（画外音，兴奋）："
            "“AI翻译、AI回复、AI视频生成，全部自动完成。聊天、支付、办公、社群，一次拥有。”"
            "音效：快速的信息流声音。\n"
            "所有主体最后一秒尽可能全身正对着镜头。"
        )

        text = local_tts.prepare_narration_text(prompt)

        self.assertEqual(
            text,
            "刚刚体验完，我真的被震惊了。它不仅是聊天软件，更是AI8video 超级平台。 "
            "AI翻译、AI回复、AI视频生成，全部自动完成。聊天、支付、办公、社群，一次拥有",
        )
        self.assertNotIn("音效", text)
        self.assertNotIn("最后一秒", text)

    def test_attach_local_tts_replaces_original_audio_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
            video = Path(tempdir) / "video.mp4"
            video.write_bytes(b"video")

            def fake_synthesize(_text, output_path, *, settings=None, ffmpeg_bin=None):
                Path(output_path).write_bytes(b"audio")
                return {"status": "generated", "path": str(output_path)}

            try:
                with patch.object(local_tts, "local_tts_status", return_value={
                    "enabled": True,
                    "available": True,
                    "availabilityReason": "ok",
                    "engine": "mimo-api",
                    "voice": "冰糖",
                    "rate": 185,
                    "volume": 1,
                }), patch.object(local_tts, "synthesize_local_tts", side_effect=fake_synthesize), \
                        patch.object(local_tts, "_fit_tts_audio_to_video_duration", return_value={
                            "status": "skipped",
                            "reason": "audio already fits",
                        }) as fit_duration, \
                        patch.object(local_tts, "_replace_video_audio_with_tts", return_value={
                            "status": "mixed",
                            "video": str(video),
                            "originalAudio": "missing",
                        }) as replace_audio:
                    result = local_tts.attach_local_tts_to_video(
                        video,
                        narration_text="台词/口播：这是本地配音。",
                        preserve_original_audio=False,
                        ffmpeg_bin="ffmpeg-test",
                    )
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(result["originalAudio"], "replaced")
        self.assertEqual(result["ttsDurationFit"]["status"], "skipped")
        fit_duration.assert_called_once()
        replace_audio.assert_called_once()

    def test_attach_local_tts_passes_video_duration_target_to_mimo(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
            video = Path(tempdir) / "video.mp4"
            video.write_bytes(b"video")
            captured_settings: dict[str, object] = {}

            def fake_synthesize(_text, output_path, *, settings=None, ffmpeg_bin=None):
                captured_settings.update(settings or {})
                Path(output_path).write_bytes(b"audio")
                return {"status": "generated", "path": str(output_path)}

            try:
                with patch.object(local_tts, "local_tts_status", return_value={
                    "enabled": True,
                    "available": True,
                    "availabilityReason": "ok",
                    "engine": "mimo-api",
                    "voice": "冰糖",
                    "rate": 185,
                    "volume": 1,
                }), patch.object(local_tts, "probe_media_duration_seconds", return_value=20.0), \
                        patch.object(local_tts, "synthesize_local_tts", side_effect=fake_synthesize), \
                        patch.object(local_tts, "_fit_tts_audio_to_video_duration", return_value={
                            "status": "skipped",
                            "reason": "audio already fits",
                        }) as fit_duration, \
                        patch.object(local_tts, "_mix_tts_audio", return_value={
                            "status": "mixed",
                            "video": str(video),
                        }):
                    result = local_tts.attach_local_tts_to_video(
                        video,
                        narration_text="台词/口播：这是本地配音。",
                        ffmpeg_bin="ffmpeg-test",
                    )
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(result["status"], "mixed")
        self.assertEqual(captured_settings["videoDurationSeconds"], 20.0)
        self.assertEqual(captured_settings["targetDurationSeconds"], 19.0)
        self.assertTrue(captured_settings["durationAutoSpeed"])
        self.assertEqual(result["targetDurationSeconds"], 19.0)
        self.assertEqual(fit_duration.call_args.kwargs["target_duration_seconds"], 19.0)

    def test_tts_duration_target_for_video_leaves_end_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            video.write_bytes(b"video")
            with patch.object(local_tts, "probe_media_duration_seconds", return_value=10.0):
                target = local_tts._tts_duration_target_for_video(video, ffmpeg_bin="ffmpeg-test")

        self.assertEqual(target["videoDurationSeconds"], 10.0)
        self.assertEqual(target["targetDurationSeconds"], 9.0)
        self.assertEqual(target["guardSeconds"], 1.0)

    def test_fit_tts_audio_to_video_duration_speeds_up_long_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            audio = Path(tempdir) / "tts.m4a"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: int) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"fitted")

            with patch.object(local_tts, "probe_media_duration_seconds", side_effect=[20.0, 24.0]), \
                    patch.object(local_tts.subprocess, "run", side_effect=fake_run):
                result = local_tts._fit_tts_audio_to_video_duration(
                    audio,
                    video,
                    ffmpeg_bin="ffmpeg-test",
                )

            self.assertEqual(result["status"], "fitted")
            self.assertEqual(result["tempo"], 1.2)
            self.assertEqual(audio.read_bytes(), b"fitted")
            self.assertEqual(commands[0][0], "ffmpeg-test")
            self.assertIn("-filter:a", commands[0])
            self.assertEqual(commands[0][commands[0].index("-filter:a") + 1], "atempo=1.2")
            self.assertIn("-t", commands[0])
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "20.000")

    def test_fit_tts_audio_to_video_duration_uses_supplied_target(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            audio = Path(tempdir) / "tts.m4a"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: int) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"fitted")

            with patch.object(local_tts, "probe_media_duration_seconds", side_effect=[20.0, 24.0]), \
                    patch.object(local_tts.subprocess, "run", side_effect=fake_run):
                result = local_tts._fit_tts_audio_to_video_duration(
                    audio,
                    video,
                    target_duration_seconds=19.0,
                    ffmpeg_bin="ffmpeg-test",
                )

            self.assertEqual(result["status"], "fitted")
            self.assertEqual(result["targetDurationSeconds"], 19.0)
            self.assertEqual(result["tempo"], 1.2632)
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "19.000")

    def test_mix_tts_audio_uses_video_duration_instead_of_shortest(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            audio = Path(tempdir) / "tts.m4a"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: int) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"mixed")

            with patch.object(local_tts, "probe_media_duration_seconds", return_value=20.0), \
                    patch.object(local_tts.subprocess, "run", side_effect=fake_run):
                result = local_tts._mix_tts_audio(
                    video,
                    audio,
                    settings={"volume": 1.7, "originalAudioVolume": 0.01},
                    ffmpeg_bin="ffmpeg-test",
                )

            self.assertEqual(result["status"], "mixed")
            self.assertEqual(video.read_bytes(), b"mixed")
            self.assertEqual(commands[0][0], "ffmpeg-test")
            self.assertNotIn("-shortest", commands[0])
            self.assertIn("-t", commands[0])
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "20.000")
            filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertIn("volume=0.01,apad[orig]", filter_complex)
            self.assertIn("volume=1.7,apad[tts]", filter_complex)
            self.assertIn("amix=inputs=2:duration=longest:dropout_transition=0:normalize=0", filter_complex)

    def test_replace_video_audio_with_tts_pads_to_video_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            video = Path(tempdir) / "video.mp4"
            audio = Path(tempdir) / "tts.m4a"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            commands: list[list[str]] = []

            def fake_run(cmd: list[str], check: bool, capture_output: bool, text: bool, timeout: int) -> None:
                commands.append(cmd)
                Path(cmd[-1]).write_bytes(b"tts-only")

            with patch.object(local_tts, "probe_media_duration_seconds", return_value=10.0), \
                    patch.object(local_tts.subprocess, "run", side_effect=fake_run):
                result = local_tts._replace_video_audio_with_tts(video, audio, "1.7", "ffmpeg-test")

            self.assertEqual(result["status"], "mixed")
            self.assertEqual(result["fallback"], "tts_only")
            self.assertEqual(video.read_bytes(), b"tts-only")
            self.assertNotIn("-shortest", commands[0])
            self.assertIn("-t", commands[0])
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "10.000")
            filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
            self.assertIn("volume=1.7,apad[aout]", filter_complex)

    def test_synthesize_local_tts_passes_preview_output_volume_to_converter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "preview.m4a"

            def fake_mimo(_text, output_path, _settings):
                Path(output_path).write_bytes(b"wav")

            def fake_convert(_source, target_path, *, ffmpeg_bin=None, volume_multiplier=None):
                Path(target_path).write_bytes(b"m4a")

            with patch.object(local_tts, "_synthesize_with_mimo_api", side_effect=fake_mimo), \
                    patch.object(local_tts, "_convert_audio_to_m4a", side_effect=fake_convert) as convert:
                result = local_tts.synthesize_local_tts(
                    "试听文本",
                    output,
                    settings={"engine": "mimo-api", "apiKey": "mimo-test-key", "volume": 1.6},
                    output_volume=1.6,
                )

        self.assertEqual(result["status"], "generated")
        convert.assert_called_once()
        self.assertEqual(convert.call_args.kwargs["volume_multiplier"], 1.6)

    def test_synthesize_local_tts_calls_mimo_api(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "preview.m4a"

            class FakeResponse:
                ok = True
                status_code = 200
                reason = "OK"

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "audio": {
                                        "data": base64.b64encode(b"wav-bytes").decode("ascii"),
                                    }
                                }
                            }
                        ]
                    }

            def fake_convert(_source, target_path, *, ffmpeg_bin=None, volume_multiplier=None):
                Path(target_path).write_bytes(b"m4a")

            with patch.object(local_tts.requests, "post", return_value=FakeResponse()) as post, \
                    patch.object(local_tts, "_convert_audio_to_m4a", side_effect=fake_convert):
                result = local_tts.synthesize_local_tts(
                    "试听文本",
                    output,
                    settings={
                        "engine": "mimo-api",
                        "apiBaseUrl": "https://api.xiaomimimo.com/v1",
                        "apiKey": "mimo-test-key",
                        "model": "mimo-v2.5-tts",
                        "voice": "冰糖",
                        "rate": 185,
                    },
                    output_volume=1.2,
                )

        self.assertEqual(result["status"], "generated")
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["api-key"], "mimo-test-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "mimo-v2.5-tts")
        self.assertEqual(post.call_args.kwargs["json"]["audio"]["voice"], "冰糖")
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("请只朗读 assistant 消息中的文本", messages[0]["content"])
        self.assertIn("自然清晰的短视频口播", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "assistant", "content": "试听文本"})

    def test_synthesize_local_tts_tells_mimo_to_fit_target_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "preview.m4a"

            class FakeResponse:
                ok = True
                status_code = 200
                reason = "OK"

                def json(self):
                    return {
                        "choices": [
                            {
                                "message": {
                                    "audio": {
                                        "data": base64.b64encode(b"wav-bytes").decode("ascii"),
                                    }
                                }
                            }
                        ]
                    }

            def fake_convert(_source, target_path, *, ffmpeg_bin=None, volume_multiplier=None):
                Path(target_path).write_bytes(b"m4a")

            with patch.object(local_tts.requests, "post", return_value=FakeResponse()) as post, \
                    patch.object(local_tts, "_convert_audio_to_m4a", side_effect=fake_convert):
                result = local_tts.synthesize_local_tts(
                    "这是一段必须完整读完的台词。",
                    output,
                    settings={
                        "engine": "mimo-api",
                        "apiBaseUrl": "https://api.xiaomimimo.com/v1",
                        "apiKey": "mimo-test-key",
                        "model": "mimo-v2.5-tts",
                        "voice": "冰糖",
                        "rate": 220,
                        "videoDurationSeconds": 10.0,
                        "targetDurationSeconds": 9.0,
                        "durationAutoSpeed": True,
                    },
                )

        self.assertEqual(result["status"], "generated")
        instruction = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("9.00 秒内自然读完", instruction)
        self.assertIn("对应视频实际时长约 10.00 秒", instruction)
        self.assertIn("当前为 220", instruction)
        self.assertEqual(
            post.call_args.kwargs["json"]["messages"][1],
            {"role": "assistant", "content": "这是一段必须完整读完的台词。"},
        )

    def test_convert_audio_to_m4a_can_apply_volume_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source = Path(tempdir) / "source.wav"
            target = Path(tempdir) / "target.m4a"
            source.write_bytes(b"wav")
            ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
            if not ffmpeg:
                self.skipTest("ffmpeg not available")
            with patch("subprocess.run") as run:
                local_tts._convert_audio_to_m4a(source, target, ffmpeg_bin=ffmpeg, volume_multiplier=0.4)

        args = run.call_args.args[0]
        self.assertIn("-filter:a", args)
        audio_filter = args[args.index("-filter:a") + 1]
        self.assertIn(local_tts.LOCAL_TTS_LOUDNESS_FILTER, audio_filter)
        self.assertIn("volume=0.4", audio_filter)

    def test_update_local_tts_settings_clamps_volume_to_new_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            env_backup = os.environ.get("AI8VIDEO_LOCAL_TTS_DIR")
            try:
                os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = str(Path(tempdir) / "tts")
                status = local_tts.update_local_tts_settings({"volume": 9})
            finally:
                if env_backup is None:
                    os.environ.pop("AI8VIDEO_LOCAL_TTS_DIR", None)
                else:
                    os.environ["AI8VIDEO_LOCAL_TTS_DIR"] = env_backup

        self.assertEqual(status["volume"], 4.0)


if __name__ == "__main__":
    unittest.main()

from Agents.BaseAgent import BaseAgent
from Errors import ConfigurationError, ValidationError

IMAGE_PLAN_OUTPUT_FORMAT = {
    "type": "object",
    "properties": {
        "total_duration_seconds": {
            "type": "number"
        },
        "image_count": {
            "type": "integer"
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {
                        "type": "integer"
                    },
                    "start_seconds": {
                        "type": "number"
                    },
                    "end_seconds": {
                        "type": "number"
                    },
                    "duration_seconds": {
                        "type": "number"
                    },
                    "narration_summary": {
                        "type": "string"
                    },
                    "visual_description": {
                        "type": "string"
                    },
                    "image_prompt": {
                        "type": "string"
                    },
                    "transition": {
                        "type": "string",
                        "enum": ["draw", "cut", "fade"]
                    }
                },
                "required": [
                    "scene_id",
                    "start_seconds",
                    "end_seconds",
                    "duration_seconds",
                    "narration_summary",
                    "visual_description",
                    "image_prompt",
                    "transition"
                ],
            }
        }
    },
    "required": [
        "total_duration_seconds",
        "image_count",
        "scenes"
    ],
}

ALLOWED_TRANSITIONS = ("draw", "cut", "fade")
DEFAULT_DURATION_TOLERANCE_SECONDS = 0.5


class ImagePlannerAgent(BaseAgent):
    """Lập kế hoạch cảnh/ảnh đồng bộ với thời lượng audio thực tế.

    Kế hoạch phải liên tục trên timeline, số ảnh nằm trong giới hạn cấu hình
    và tổng thời lượng khớp audio trong sai số cho phép
    (``duration_tolerance_seconds``).
    """

    def __init__(self, config, llm_client, log_service=None):
        super().__init__(
            llm_client,
            config["role"],
            log_service=log_service,
            json_max_attempts=config.get("json_max_attempts"),
        )

        self._prompt = config["prompt"]
        self._min_images = int(config["min_images"])
        self._max_images = int(config["max_images"])
        self._duration_tolerance = float(
            config.get(
                "duration_tolerance_seconds", DEFAULT_DURATION_TOLERANCE_SECONDS
            )
        )

        if self._min_images < 1:
            raise ConfigurationError("min_images phải lớn hơn hoặc bằng 1.")

        if self._max_images < self._min_images:
            raise ConfigurationError(
                "max_images phải lớn hơn hoặc bằng min_images."
            )

        if self._duration_tolerance < 0:
            raise ConfigurationError(
                "duration_tolerance_seconds không được âm."
            )

    @property
    def duration_tolerance(self):
        return self._duration_tolerance

    def run(self, article, tts_text, tts_duration):
        self._validate_inputs(article, tts_text, tts_duration)

        title = article.get("title", "Không có tiêu đề")
        summary = article.get("des", "")

        task = f"""
            {self._prompt}

            THÔNG TIN BÀI VIẾT:
            Tiêu đề: {title}

            Tóm tắt bài viết:
            {summary}

            KỊCH BẢN ĐƯỢC DÙNG ĐỂ TẠO AUDIO:
            {tts_text}

            THỜI LƯỢNG AUDIO THỰC TẾ:
            {tts_duration} giây

            GIỚI HẠN SỐ ẢNH:
            - Số ảnh tối thiểu: {self._min_images}
            - Số ảnh tối đa: {self._max_images}

            YÊU CẦU BẮT BUỘC:
            1. Chia toàn bộ kịch bản thành các cảnh theo đúng trình tự.
            2. Mỗi cảnh phải minh họa một ý chính của lời đọc tương ứng.
            3. Chọn số ảnh trong khoảng giới hạn đã cung cấp.
            4. Cảnh đầu bắt đầu tại giây 0.
            5. Các cảnh liên tiếp nhau, không có khoảng trống hoặc chồng lấn.
            6. Cảnh cuối kết thúc đúng tại giây {tts_duration}.
            7. duration_seconds = end_seconds - start_seconds.
            8. total_duration_seconds phải bằng {tts_duration}.
            9. image_count phải bằng số phần tử trong scenes.
            10. image_prompt phải là prompt tiếng Anh mô tả một hình ảnh tĩnh.
            11. Phong cách thống nhất: hand-drawn whiteboard illustration,
                black marker line art, pure white background, simple composition.
            12. Không yêu cầu AI tạo ảnh viết chữ dài, phụ đề hoặc đoạn văn
                trực tiếp lên hình.
            13. Không tự bịa dữ kiện hoặc chi tiết không có căn cứ từ bài viết.
            14. Phân bổ thời lượng dựa trên nội dung lời đọc và độ phức tạp
                của từng cảnh, không chia đều một cách máy móc.
            15. Chỉ trả về JSON theo schema được cung cấp, không thêm Markdown.
            """

        plan = self.generate_json(
            task,
            IMAGE_PLAN_OUTPUT_FORMAT,
            max_attempts=self._json_max_attempts,
            validator=lambda data: self._prepare_plan(data, tts_duration),
        )

        return plan

    # ------------------------------------------------------------------
    # Kiểm tra
    # ------------------------------------------------------------------

    def _prepare_plan(self, plan, tts_duration):
        """Chuẩn hoá tất định rồi kiểm tra ngữ nghĩa.

        Chạy trong ``generate_json`` nên lỗi ngữ nghĩa được gửi lại cho model
        kèm yêu cầu sửa (repair loop), thay vì làm hỏng cả bài ngay.
        """
        changes = self._normalize_plan(plan, tts_duration)

        if changes:
            self.log_info(
                "image_plan_normalized",
                f"Chuẩn hoá {len(changes)} trường suy dẫn của kế hoạch ảnh.",
                changed=changes,
            )

        self._validate_plan(plan, tts_duration)

    def _normalize_plan(self, plan, tts_duration):
        """Sửa các trường dẫn xuất và làm phẳng sai lệch nhỏ trên timeline.

        Trả về danh sách tên trường đã sửa (rỗng nếu không cần gì).

        Chỉ sửa những gì suy ra được từ dữ liệu cảnh:
        ``image_count``, ``scene_id``, ``duration_seconds``,
        ``total_duration_seconds``; và snap ``start_seconds``/``end_seconds``
        khi lệch trong sai số cho phép. Lệch lớn hơn được giữ nguyên để
        ``_validate_plan`` báo lỗi và kích hoạt repair.
        """
        changes = []

        if not isinstance(plan, dict):
            return changes

        scenes = plan.get("scenes")

        if not isinstance(scenes, list) or not scenes:
            return changes

        if not self._same_number(plan.get("image_count"), len(scenes)):
            plan["image_count"] = len(scenes)
            changes.append("image_count")

        expected_start = 0.0

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue

            if not self._same_number(scene.get("scene_id"), index):
                scene["scene_id"] = index
                changes.append(f"scenes[{index}].scene_id")

            start = scene.get("start_seconds")
            end = scene.get("end_seconds")

            if not self._is_number(start) or not self._is_number(end):
                continue

            if start != expected_start and (
                abs(start - expected_start) <= self._duration_tolerance
            ):
                scene["start_seconds"] = expected_start
                start = expected_start
                changes.append(f"scenes[{index}].start_seconds")

            if index == len(scenes) and end != tts_duration and (
                abs(end - tts_duration) <= self._duration_tolerance
            ):
                scene["end_seconds"] = tts_duration
                end = tts_duration
                changes.append(f"scenes[{index}].end_seconds")

            duration = scene.get("duration_seconds")
            expected_duration = round(end - start, 3)

            if duration != expected_duration:
                scene["duration_seconds"] = expected_duration
                changes.append(f"scenes[{index}].duration_seconds")

            expected_start = end

        if plan.get("total_duration_seconds") != expected_start:
            plan["total_duration_seconds"] = expected_start
            changes.append("total_duration_seconds")

        return changes

    @staticmethod
    def _validate_inputs(article, tts_text, tts_duration):
        if not isinstance(article, dict):
            raise ValidationError("article phải là dictionary.")

        if not ImagePlannerAgent._is_number(tts_duration) or tts_duration <= 0:
            raise ValidationError("Thời lượng audio không hợp lệ.")

        if not isinstance(tts_text, str) or not tts_text.strip():
            raise ValidationError("Kịch bản TTS không được để trống.")

    def _validate_plan(self, plan, tts_duration):
        if not isinstance(plan, dict):
            raise ValidationError("Image plan phải là JSON object.")

        scenes = plan.get("scenes")

        if not isinstance(scenes, list) or not scenes:
            raise ValidationError("scenes phải là danh sách không rỗng.")

        image_count = len(scenes)

        if not self._min_images <= image_count <= self._max_images:
            raise ValidationError(
                f"Số cảnh là {image_count}, nằm ngoài giới hạn "
                f"{self._min_images}-{self._max_images}."
            )

        if not self._same_number(plan.get("image_count"), image_count):
            raise ValidationError(
                f"image_count ({plan.get('image_count')!r}) không khớp số "
                f"cảnh thực tế ({image_count})."
            )

        total_duration = plan.get("total_duration_seconds")

        if not self._is_number(total_duration):
            raise ValidationError("Tổng thời lượng không hợp lệ.")

        if abs(total_duration - tts_duration) > self._duration_tolerance:
            raise ValidationError(
                f"Tổng thời lượng kế hoạch ({total_duration}s) không khớp "
                f"thời lượng audio ({tts_duration}s)."
            )

        expected_start = 0.0

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                raise ValidationError(f"Cảnh {index} phải là dictionary.")

            if not self._same_number(scene.get("scene_id"), index):
                raise ValidationError(
                    f"scene_id của cảnh {index} không hợp lệ."
                )

            start = scene.get("start_seconds")
            end = scene.get("end_seconds")
            duration = scene.get("duration_seconds")

            if not all(
                self._is_number(value) for value in (start, end, duration)
            ):
                raise ValidationError(
                    f"Cảnh {index} có timestamp không hợp lệ."
                )

            if abs(start - expected_start) > self._duration_tolerance:
                raise ValidationError(
                    f"Cảnh {index} bắt đầu ở {start}s nhưng phải là "
                    f"{expected_start}s: timeline bị hở hoặc chồng lấn."
                )

            if end <= start:
                raise ValidationError(
                    f"Cảnh {index} có thời lượng không hợp lệ "
                    f"(start={start}s, end={end}s)."
                )

            if abs((end - start) - duration) > self._duration_tolerance:
                raise ValidationError(
                    f"duration_seconds của cảnh {index} là {duration}s nhưng "
                    f"end_seconds - start_seconds = {round(end - start, 3)}s."
                )

            for field in (
                "narration_summary",
                "visual_description",
                "image_prompt",
            ):
                if (
                    not isinstance(scene.get(field), str)
                    or not scene[field].strip()
                ):
                    raise ValidationError(f"Cảnh {index} thiếu {field}.")

            if scene.get("transition") not in ALLOWED_TRANSITIONS:
                raise ValidationError(
                    f"Transition của cảnh {index} không hợp lệ."
                )

            expected_start = end

        if abs(expected_start - tts_duration) > self._duration_tolerance:
            raise ValidationError(
                f"Cảnh cuối kết thúc ở {expected_start}s nhưng thời lượng "
                f"audio là {tts_duration}s."
            )

    @staticmethod
    def _is_number(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )

    @classmethod
    def _same_number(cls, value, expected):
        """So sánh số cho phép model trả về 5.0 thay vì 5."""
        return cls._is_number(value) and int(value) == int(expected)

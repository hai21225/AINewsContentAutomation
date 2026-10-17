
import json

from Agents.BaseAgent import BaseAgent


OUT_PUT_FORMAT = {
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


class ImagePlannerAgent(BaseAgent):

    def __init__(self, config, llmClient):
        super().__init__(llmClient, config["role"])

        self.prompt = config["prompt"]
        self.min_images = int(config["min_images"])
        self.max_images = int(config["max_images"])

        if self.min_images < 1:
            raise ValueError("min_images phải lớn hơn hoặc bằng 1")

        if self.max_images < self.min_images:
            raise ValueError(
                "max_images phải lớn hơn hoặc bằng min_images"
            )

    def run(self, article, ttsText, ttsduration):
        # 1. Validate input
        if ttsduration is None or ttsduration <= 0:
            raise ValueError("Thời lượng audio không hợp lệ")

        if not isinstance(ttsText, str) or not ttsText.strip():
            raise ValueError("Kịch bản TTS không được để trống")

        if not isinstance(article, dict):
            raise ValueError("article phải là dictionary")

        title = article.get("title", "Không có tiêu đề")
        summary = (
            article.get("des")
            or ""
        )

        # 2. Build task cho LLM
        task = f"""
            {self.prompt}

            THÔNG TIN BÀI VIẾT:
            Tiêu đề: {title}

            Tóm tắt bài viết:
            {summary}

            KỊCH BẢN ĐƯỢC DÙNG ĐỂ TẠO AUDIO:
            {ttsText}

            THỜI LƯỢNG AUDIO THỰC TẾ:
            {ttsduration} giây

            GIỚI HẠN SỐ ẢNH:
            - Số ảnh tối thiểu: {self.min_images}
            - Số ảnh tối đa: {self.max_images}

            YÊU CẦU BẮT BUỘC:
            1. Chia toàn bộ kịch bản thành các cảnh theo đúng trình tự.
            2. Mỗi cảnh phải minh họa một ý chính của lời đọc tương ứng.
            3. Chọn số ảnh trong khoảng giới hạn đã cung cấp.
            4. Cảnh đầu bắt đầu tại giây 0.
            5. Các cảnh liên tiếp nhau, không có khoảng trống hoặc chồng lấn.
            6. Cảnh cuối kết thúc đúng tại giây {ttsduration}.
            7. duration_seconds = end_seconds - start_seconds.
            8. total_duration_seconds phải bằng {ttsduration}.
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

        # 3. Gọi LLM thông qua BaseAgent
        response = self.generate(
            task,
            OUT_PUT_FORMAT
        )

        # 4. Parse JSON
        plan = self._parse_response(response)

        # 5. Validate kết quả
        self._validate_plan(plan, ttsduration)

        return plan

    def _parse_response(self, response):
        if isinstance(response, dict):
            return response

        if not isinstance(response, str):
            raise ValueError(
                "LLM trả về kiểu dữ liệu không hợp lệ"
            )

        try:
            plan = json.loads(response)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"LLM trả về JSON không hợp lệ: {error}"
            ) from error

        if not isinstance(plan, dict):
            raise ValueError("Image plan phải là JSON object")

        return plan

    def _validate_plan(self, plan, ttsduration):
        scenes = plan.get("scenes")

        if not isinstance(scenes, list):
            raise ValueError("scenes phải là một danh sách")

        image_count = len(scenes)

        if not self.min_images <= image_count <= self.max_images:
            raise ValueError(
                f"Số ảnh {image_count} nằm ngoài giới hạn "
                f"{self.min_images}-{self.max_images}"
            )

        if plan.get("image_count") != image_count:
            raise ValueError(
                "image_count không khớp số cảnh thực tế"
            )

        tolerance = 0.05

        total_duration = plan.get("total_duration_seconds")

        if not self._is_number(total_duration):
            raise ValueError("Tổng thời lượng không hợp lệ")

        if abs(total_duration - ttsduration) > tolerance:
            raise ValueError(
                "Tổng thời lượng kế hoạch không khớp audio"
            )

        expected_start = 0.0

        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                raise ValueError(
                    f"Cảnh {index} phải là dictionary"
                )

            if scene.get("scene_id") != index:
                raise ValueError(
                    f"scene_id của cảnh {index} không hợp lệ"
                )

            start = scene.get("start_seconds")
            end = scene.get("end_seconds")
            duration = scene.get("duration_seconds")

            if not all(
                self._is_number(value)
                for value in (start, end, duration)
            ):
                raise ValueError(
                    f"Cảnh {index} có timestamp không hợp lệ"
                )

            if abs(start - expected_start) > tolerance:
                raise ValueError(
                    f"Cảnh {index} bị hở hoặc chồng lấn timeline"
                )

            if end <= start:
                raise ValueError(
                    f"Cảnh {index} có thời lượng không hợp lệ"
                )

            if abs((end - start) - duration) > tolerance:
                raise ValueError(
                    f"duration_seconds của cảnh {index} sai"
                )

            for field in (
                "narration_summary",
                "visual_description",
                "image_prompt"
            ):
                if (
                    not isinstance(scene.get(field), str)
                    or not scene[field].strip()
                ):
                    raise ValueError(
                        f"Cảnh {index} thiếu {field}"
                    )

            if scene.get("transition") not in (
                "draw", "cut", "fade"
            ):
                raise ValueError(
                    f"Transition của cảnh {index} không hợp lệ"
                )

            expected_start = end

        if abs(expected_start - ttsduration) > tolerance:
            raise ValueError(
                "Cảnh cuối chưa kết thúc đúng thời lượng audio"
            )

    @staticmethod
    def _is_number(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )

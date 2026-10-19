# Nature Photo Cases

This folder contains curated photo-oriented examples rendered with the local Informative Drawings provider and the contour-aware color fill pipeline.

| Case | Input | Preview | Output |
| --- | --- | --- | --- |
| Pool | <img src="pool.jpg" alt="Pool input" width="180"> | <a href="pool.mp4"><img src="pool-preview.gif" alt="Pool whiteboard preview" width="180"></a> | [MP4](pool.mp4) |
| Interior | <img src="cool.jpg" alt="Interior input" width="180"> | <a href="cool.mp4"><img src="cool-preview.gif" alt="Interior whiteboard preview" width="180"></a> | [MP4](cool.mp4) |
| Portrait | <img src="girl.jpg" alt="Portrait input" width="180"> | <a href="girl.mp4"><img src="girl-preview.gif" alt="Portrait whiteboard preview" width="180"></a> | [MP4](girl.mp4) |
| Sports | <img src="halande.jpg" alt="Sports input" width="180"> | <a href="halande.mp4"><img src="halande-preview.gif" alt="Sports whiteboard preview" width="180"></a> | [MP4](halande.mp4) |

Representative command:

```bash
whiteboard render-photo examples/cases/nature/pool.jpg \
  -o out/nature-pool.mp4 \
  --duration 15 \
  --fps 30 \
  --lineart-provider informative \
  --stroke-detail rich \
  --hand asian \
  --tail-color 4.5 \
  --color-fill contour-wipe
```

For faster local previews, add explicit dimensions such as `--width 720 --height 1284`, or lower `--duration` and `--fps`.

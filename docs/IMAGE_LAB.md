# Image Lab and OCR

Choose a processing mode, click an area of the first uploaded image, and request a small preview. Only a crop up to 160 × 160 pixels is submitted. Completed previews are cached in browser memory for the current file, crop, scale and strength; switching back does not submit another job. Changing the crop or file clears that cache. Preview jobs are deleted after downloading their results. Preview and full-image jobs retain the existing private capability-token authorization and expiry rules.

## Models and credits

Model weights are separate works from this AGPL application. The application runs inference without changing the weights. Output blending, tiling, resizing and alpha restoration happen outside the models. Download locations and verified SHA-256 values are recorded in [models.json](../ai-backends/models.json).

| Mode | Model and author | Native scale | Upstream license |
| --- | --- | --- | --- |
| General | [Real-ESRGAN, Xintao Wang and contributors](https://github.com/xinntao/Real-ESRGAN) | 2× / 4× | BSD-3-Clause |
| Photo | [2xPublic RealPLKSR, Philip Hofmann (Phhofm)](https://github.com/Phhofm/models/releases/tag/2xPublic_realplksr_dysample_layernorm_real) | 2× | Apache-2.0 |
| Web photos | [4xRealWebPhoto v3 ATD, Philip Hofmann (Phhofm)](https://github.com/Phhofm/models/releases/tag/4xRealWebPhoto_v3_atd) | 4× | CC BY 4.0 |
| Detail | [Real-HAT-GAN, XPixelGroup](https://github.com/XPixelGroup/HAT), [weight mirror](https://huggingface.co/Acly/hat) | 4× | Apache-2.0 |
| Illustrations / anime | [RealESRGAN x4plus anime 6B, Xintao Wang and contributors](https://github.com/xinntao/Real-ESRGAN) | 4× | BSD-3-Clause |
| Enhance without enlargement | [1xGater v3 Restore, Philip Hofmann (Phhofm)](https://github.com/Phhofm/models/releases/tag/1xgaterv3_r_restore) | 1× | CC BY 4.0 |
| Background: fast / quality / portrait | [BiRefNet, Zheng Peng and contributors](https://github.com/ZhengPeng7/BiRefNet), [ONNX distributions by rembg](https://github.com/danielgatis/rembg) | Original image dimensions | See upstream MIT licenses |
| Face reconstruction | [GFPGAN v1.4, TencentARC](https://github.com/TencentARC/GFPGAN), [FaceXLib detection and parsing, Xintao Wang](https://github.com/xinntao/facexlib) | Aligned 512 px faces, pasted into 1×/2×/4× output | [GFPGAN Apache-2.0 and third-party notices](https://github.com/TencentARC/GFPGAN/blob/master/LICENSE), FaceXLib MIT |

The licenses are available from the linked upstream projects: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0). These credits do not imply endorsement by the authors.

## Processing and performance

Upscale weights load once when the GPU service starts and remain resident. No model download occurs when a visitor switches modes. All six GPU weight sets together occupied about 333 MiB in a GTX 1070 test (inference activations require additional VRAM). FP32 and overlapping tiles are used to bound memory. Increasing output size also increases compute time: a small preview is not a full-image speed guarantee. Web photos and Detail are substantially slower than the General and Illustration modes.

PNG and WebP retain transparency. JPEG uses a white background. The enhancement strength blends the restored RGB with the original; it does not change the alpha mask. Before/after comparison supports a movable boundary, zoom and dragging.

Comparison clips both images independently over a checkerboard: transparent result pixels cannot reveal the original. Before is on the left, After on the right. Drag the divider directly or use the range input; separate buttons show the complete original or result.

Optional face reconstruction uses the pure PyTorch clean GFPGAN v1.4 architecture, RetinaFace detection and ParseNet blending. Models load at service startup. Off (default), Gentle (50% restored face) and Strong (100%) work with every upscale mode and with native 1× enhancement. The original alpha is retained. Reconstruction runs once at the final AI stage in Combo, under the same GPU lock as upscaling. Native 1× requests with face reconstruction go to the GPU service; ordinary 1× cleanup stays on CPU. Detection is bounded to a 1024 px long side and at most 16 detected faces; output is limited to 32 megapixels when reconstruction is enabled. If no face is detected, the selected upscale/cleanup still runs. GFPGAN reconstructs plausible details and can change identity; it cannot reliably recover obscured areas or missing hair texture. This is not whole-image diffusion regeneration.

Face previews use the entire image reduced to at most 256 px per side, rather than a crop that could cut off the face. Their cache includes the face reconstruction setting. Full-resolution results can differ. The pinned BasicSR package receives a documented torchvision compatibility import fix in the GPU Dockerfile; no custom CUDA extensions are used.

Background removal runs on CPU and offers General Lite, full General and Portrait BiRefNet models. Its model files are persistent on disk, but only the selected background session stays in RAM. Switching background presets can therefore include local initialization. Requests are serialized to bound memory. The CPU service also supports native 1× restoration at `/enhance`.

Prepare images chains optional background removal, native 1× restoration, optional 2×/4× upscaling, a transparent or solid background, proportional maximum dimensions and export. Batches are returned as ZIP files. Maximum dimensions are a bounding box; the image is not stretched.

OCR is in Converter → Documents → Scan / photo → text. Tesseract recognizes Russian, English or both, returning editable DOCX, UTF-8 TXT or PDF with a searchable text layer. Existing PDF text is retained where available. Scanned pages are processed separately, with a 50-page job limit and bounded raster size. DOCX preserves recognized paragraphs and page breaks rather than reconstructing exact tables or page design. OCR results should be checked against the source.

## API compatibility

Existing operations, job creation/status/download/cancellation, capability headers and `/api/metrics` remain available. Additive operation names are `upscale-preview`, `image-enhance`, `image-pipeline` and `ocr`; these appear in the existing metrics operation breakdown. Preview requests have their own hourly rate bucket and accept one image no larger than 192 pixels on either side.

`upscale` additionally accepts `model` (`standard`, `photo`, `web-photo`, `detail`, `illustration`) and `strength` (0–100). The photo model requires 2×. Background options accept `backgroundPreset` (`fast`, `quality`, `portrait`). OCR accepts `format` (`docx`, `txt`, `pdf`) and `language` (`rus+eng`, `rus`, `eng`). Existing defaults and full-job rate limits remain compatible.

`faceRestoration` accepts `0`, `50`, or `100` for upscale, preview, enhancement and pipeline jobs. It is forwarded as `face_restoration` to the GPU backend. Existing job/result/statistics contracts are unchanged. Face previews allow 256 px instead of the ordinary 192 px request limit.

See [self-hosting](SELF_HOSTING.md) for provisioned CPU/GPU services and deployment.

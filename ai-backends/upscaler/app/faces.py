"""Optional GFPGAN restoration, called inside the shared inference lock."""
import cv2
import numpy as np
import torch
from facexlib.utils.face_restoration_helper import FaceRestoreHelper
from gfpgan.archs.gfpganv1_clean_arch import GFPGANv1Clean
from PIL import Image


class FaceRestorer:
    def __init__(self, model_dir, device):
        self.device = device
        self.model = GFPGANv1Clean(out_size=512, num_style_feat=512,
                                  channel_multiplier=2, decoder_load_path=None,
                                  fix_decoder=False, num_mlp=8, input_is_latent=True,
                                  different_w=True, narrow=1, sft_half=True)
        weights = torch.load(model_dir / 'GFPGANv1.4.pth', map_location='cpu', weights_only=True)
        self.model.load_state_dict(weights['params_ema'], strict=True)
        self.model = self.model.eval().to(device)
        self.helper = FaceRestoreHelper(1, face_size=512, use_parse=True,
                                       device=device, model_rootpath=str(model_dir))

    @torch.inference_mode()
    def restore(self, original, background, scale, amount):
        helper = self.helper
        helper.clean_all()
        try:
            helper.set_upscale_factor(scale)
            bgr = cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2BGR)
            helper.read_image(bgr)
            height, width = bgr.shape[:2]
            # Detection is bounded independently from the output resolution.
            resize = round(min(width, height) * min(1, 1024 / max(width, height)))
            helper.get_face_landmarks_5(resize=max(1, resize), eye_dist_threshold=5)
            helper.all_landmarks_5 = helper.all_landmarks_5[:16]
            helper.det_faces = helper.det_faces[:16]
            if not helper.all_landmarks_5:
                return background, 0
            helper.align_warp_face()
            for crop in helper.cropped_faces:
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                tensor = torch.from_numpy(rgb.transpose(2, 0, 1).copy()).float()
                tensor = (tensor / 127.5 - 1).unsqueeze(0).to(self.device)
                output = self.model(tensor, return_rgb=False, weight=1.0)[0][0]
                restored = ((output.clamp(-1, 1).cpu().numpy().transpose(1, 2, 0) + 1) * 127.5).round().astype('uint8')
                restored = cv2.cvtColor(restored, cv2.COLOR_RGB2BGR)
                helper.add_restored_face(cv2.addWeighted(crop, 1 - amount / 100, restored, amount / 100, 0))
            helper.get_inverse_affine(None)
            result = helper.paste_faces_to_input_image(
                upsample_img=cv2.cvtColor(np.asarray(background), cv2.COLOR_RGB2BGR))
            return Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB)), len(helper.restored_faces)
        finally:
            helper.clean_all()
            helper.input_img = None

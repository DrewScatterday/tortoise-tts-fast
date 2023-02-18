# AGPL: a notification must be added stating that changes have been made to that file.

import os

import torch
import torchaudio
import streamlit as st

from filepicker import st_file_selector

from tortoise.api import MODELS_DIR, TextToSpeech
from tortoise.utils.audio import load_voices
from tortoise.utils.diffusion import SAMPLERS

from scripts.inference import run_and_save_tts, infer_on_texts, split_and_recombine_text

from contextlib import contextmanager
from pathlib import Path
from time import time
from io import BytesIO


@contextmanager
def timeit(desc=""):
    start = time()
    yield
    print(f"{desc} took {time() - start:.2f} seconds")

LATENT_MODES = ["Tortoise original (bad)", "average per 4.27s (broken on small files)", "average per voice file (broken on small files)"]
if __name__ == "__main__":
    text = st.text_area(
        "Text",
        help="Text to speak.",
        value="The expressiveness of autoregressive transformers is literally nuts! I absolutely adore them.",
    )

    voices = os.listdir("tortoise/voices") + ["random"]
    voices.remove("cond_latent_example")
    voice = st.selectbox(
        "Voice",
        voices,
        help="Selects the voice to use for generation. See options in voices/ directory (and add your own!) "
        "Use the & character to join two voices together. Use a comma to perform inference on multiple voices.",
        index=len(voices) - 1,
    )
    preset = st.selectbox(
        "Preset",
        (
            "single_sample",
            "ultra_fast",
            "very_fast",
            "ultra_fast_old",
            "fast",
            "standard",
            "high_quality",
        ),
        help="Which voice preset to use.",
        index=1,
    )
    with st.expander("Advanced"):
        col1, col2 = st.columns(2)
        with col1:
            """#### Model parameters"""
            candidates = st.number_input(
                "Candidates", help="How many output candidates to produce per-voice.", value=3
            )
            latent_averaging_mode = st.radio(
                "Latent averaging mode",
                LATENT_MODES,
                help="How voice samples should be averaged together.",
                index=0,
            )
            sampler = st.radio(
                "Sampler",
                SAMPLERS,
                help="Diffusion sampler. Note that dpm++2m is experimental and typically requires more steps.",
                index=1
            )
            steps = st.number_input(
                "Steps",
                help="Override the steps used for diffusion (default depends on preset)",
                value=10,
            )
            seed = st.number_input(
                "Seed", help="Random seed which can be used to reproduce results.", value=-1
            )
            if seed == -1:
                seed = None
            """#### Directories"""
            output_path = st.text_input(
                "Output Path", help="Where to store outputs.", value="results/"
            )
            model_dir = st.text_input(
                "Model Directory",
                help="Where to find pretrained model checkpoints. Tortoise automatically downloads these to .models, so this"
                "should only be specified if you have custom checkpoints.",
                value=MODELS_DIR,
            )
            ar_checkpoint = st_file_selector(st, label="Select GPT Checkpoint", key='pth')


        with col2:
            """#### Optimizations"""
            high_vram = not st.checkbox(
                "Low VRAM",
                help="Re-enable default offloading behaviour of tortoise",
                value=True,
            )
            half = st.checkbox(
                "Half-Precision",
                help="Enable autocast to half precision for autoregressive model",
                value=False,
            )
            kv_cache = st.checkbox(
                "Key-Value Cache",
                help="Enable kv_cache usage, leading to drastic speedups but worse memory usage",
                value=True,
            )
            cond_free = st.checkbox(
                "Conditioning Free", help="Force conditioning free diffusion", value=True
            )
            no_cond_free = st.checkbox(
                "Force Not Conditioning Free",
                help="Force disable conditioning free diffusion",
                value=False,
            )

            """#### Text Splitting"""
            min_chars_to_split = st.number_input(
                "Min Chars to Split",
                help="Minimum number of characters to split text on",
                min_value=50,
                value=200,
                step=1
            )

            """#### Debug"""
            produce_debug_state = st.checkbox(
                "Produce Debug State",
                help="Whether or not to produce debug_state.pth, which can aid in reproducing problems. Defaults to true.",
                value=True,
            )

    ar_checkpoint = None if ar_checkpoint[-4:] != '.pth' else ar_checkpoint
    if 'tts' not in st.session_state or st.session_state.tts._config() != {
            'models_dir': model_dir, 'high_vram': high_vram, 'kv_cache': kv_cache, 'ar_checkpoint': ar_checkpoint
    }:
        st.session_state.tts = TextToSpeech(models_dir=model_dir, high_vram=high_vram, kv_cache=kv_cache, ar_checkpoint=ar_checkpoint)
    tts = st.session_state.tts
    if st.button("Start"):
        assert latent_averaging_mode
        assert preset
        assert voice
        def show_generation(g, filename: str):
            audio_buffer = BytesIO()
            torchaudio.save(audio_buffer, g, 24000, format='wav')
            st.audio(audio_buffer, format="audio/wav")
            st.download_button(
                "Download sample",
                audio_buffer,
                file_name=filename,
            )
        with st.spinner(f"Generating {candidates} candidates for voice {voice} (seed={seed}). You can see progress in the terminal"):
            os.makedirs(output_path, exist_ok=True)

            selected_voices = voice.split(",")
            for k, selected_voice in enumerate(selected_voices):
                if "&" in selected_voice:
                    voice_sel = selected_voice.split("&")
                else:
                    voice_sel = [selected_voice]
                voice_samples, conditioning_latents = load_voices(voice_sel)

                voice_path = Path(os.path.join(output_path, selected_voice))

                with timeit(
                    f"Generating {candidates} candidates for voice {selected_voice} (seed={seed})"
                ):
                    nullable_kwargs = {
                        k: v
                        for k, v in zip(
                            ["sampler", "diffusion_iterations", "cond_free"],
                            [sampler, steps, cond_free],
                        )
                        if v is not None
                    }
                    def call_tts(text: str):
                        return tts.tts_with_preset(
                            text,
                            k=candidates,
                            voice_samples=voice_samples,
                            conditioning_latents=conditioning_latents,
                            preset=preset,
                            use_deterministic_seed=seed,
                            return_deterministic_state=True,
                            cvvp_amount=0.0,
                            half=half,
                            latent_averaging_mode=LATENT_MODES.index(latent_averaging_mode),
                            **nullable_kwargs,
                        )
                    if len(text) < min_chars_to_split:
                        audios = run_and_save_tts(call_tts, text, voice_path, return_deterministic_state=True)
                        for i,audio in enumerate(audios):
                            show_generation(audio, f'{selected_voice}-text-{i}.wav')
                    else: 
                        desired_length = int(min_chars_to_split)
                        texts = split_and_recombine_text(text, desired_length, desired_length+100)
                        if candidates != 1:
                            st.warning("candidates != 1 while splitting text; only choosing the first candidate for each text fragment!", icon="⚠️")
                        audio = infer_on_texts(
                            call_tts, texts, voice_path,
                            return_deterministic_state=True,
                            lines_to_regen=set(range(len(texts)))
                        )
                        show_generation(audio, f'{selected_voice}-text.wav')
        if produce_debug_state:
            '''Debug states can be found in the output directory'''

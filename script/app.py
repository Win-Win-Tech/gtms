# -*- coding: utf-8 -*-
# Install dependencies before running:
# pip install pillow opencv-python gradio

import cv2
import numpy as np
from PIL import Image
import gradio as gr
import threading
import time
import os

# Ìæ® Convert image to pencil sketch
def convert_to_sketch(image):
    print("‚úÖ Image received")
    img = np.array(image.convert("RGB"))
    img = cv2.resize(img, (800, 800))  # Resize for faster processing

    print("Converting to sketch...")
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    inv = 255 - gray
    blur = cv2.GaussianBlur(inv, (21, 21), 0)
    sketch = cv2.divide(gray, 255 - blur, scale=256)

    sketch_rgb = cv2.cvtColor(sketch, cv2.COLOR_GRAY2RGB)
    sketch_image = Image.fromarray(sketch_rgb)

    # Ì∑® Trigger shutdown after short delay
    threading.Thread(target=shutdown_after_delay).start()

    return sketch_image, "‚úÖ Pencil sketch successfully generated."

# ‚è±Ô∏è Shutdown function
def shutdown_after_delay():
    time.sleep(3)  # Give Gradio time to render the image and message
    print("\n‚úÖ Pencil sketch successfully generated. Program will now exit.")
    os._exit(0)

# Ìºê Gradio interface
gr.Interface(
    fn=convert_to_sketch,
    inputs=gr.Image(type="pil", label="Upload your photo"),
    outputs=[
        gr.Image(type="pil", label="Pencil Sketch Output"),
        gr.Textbox(label="Status Message")
    ],
    title="Pencil Sketch Generator",
    description="Upload a JPG image to convert it into a pencil sketch using OpenCV."
).launch()

#Launch on server IP
app.launch(
    server_name="0.0.0.0",  # Binds to all available IPs
    server_port=7860,       # You can change this if needed
    share=False             # Set to True if you want a public Gradio link
)

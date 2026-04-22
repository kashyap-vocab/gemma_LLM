import cv2
import numpy as np
import argparse
import os
import pandas as pd
from ultralytics import YOLO
from paddleocr import PaddleOCR
from openai import OpenAI

def crop_image(image, x_min, y_min, x_max, y_max):
    x_min, y_min, x_max, y_max = int(x_min), int(y_min), int(x_max), int(y_max)
    return image[y_min:y_max, x_min:x_max]

VLLM_BASE_URL = "http://192.168.30.239:9000/v1"
VLLM_MODEL = "google/gemma-2-9b-it"

def get_grill_temperature(image, detector_model_path):
    """
    Detect LCD and perform OCR to get grill temperature.

    Args:
        image: OpenCV image array.
        detector_model_path: Path to the YOLO .pt model file.

    Returns:
        str: Extracted temperature or Error string.
    """
    try:
        client = OpenAI(base_url=VLLM_BASE_URL, api_key="none")
        
        # Load LCD Detector
        lcd_detector = YOLO(detector_model_path)
        res = lcd_detector.predict(image)
        
        if not res or len(res[0].boxes) == 0:
            return "No LCD detected"
            
        x_min, y_min, x_max, y_max = res[0].boxes.xyxy[0].tolist()
        cropped_image = crop_image(image, x_min, y_min, x_max, y_max)
        
        # Perform OCR using PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="en")
        result = ocr.ocr(cropped_image, det=False, cls=False)
        
        if result:
            numbers = [str(text[0]) for text in result]
            ocr_output = numbers
            prompt = (
                "The following numbers are an OCR output from a thermometer reading: "
                f"{ocr_output} "
                "Please extract the temperature in a standard numeric format with units, if present (e.g., '98.6°F' or '37°C'). "
                "But the temperature lies between -10 degree Celsius to 50 degree Celsius, so if by mistake the ocr output is something like 129, "
                "you should understand that actual temperature is 12.9 degree Celsius. Just give the final temperature as output."
            )

            response = client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        else:
            return "No text detected on LCD"
            
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grill Temperature OCR")
    parser.add_argument("--image_path", required=True, help="Path to the input image")
    parser.add_argument("--detector_model_path", required=True, help="Path to the YOLO .pt file (LCD_detector.pt)")
    args = parser.parse_args()

    if not os.path.exists(args.image_path):
        print(f"Error: Image path '{args.image_path}' does not exist.")
    elif not os.path.exists(args.detector_model_path):
        print(f"Error: Detector model path '{args.detector_model_path}' does not exist.")
    else:
        img = cv2.imread(args.image_path)
        result = get_grill_temperature(img, args.detector_model_path)
        print(f"Temperature: {result}")
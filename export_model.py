"""
CampusGuard — Model Export Script
Converts YOLO PyTorch model to ONNX for maximum stable FPS on Windows.
"""
from ultralytics import YOLO
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')

def export_to_onnx():
    # 1. Load the standard PyTorch model
    logging.info("⏳ Loading YOLO26 Medium model...")
    model = YOLO('yolo26m.pt') 
    
    # 2. Export to ONNX (Highly optimized, GPU accelerated via onnxruntime-gpu)
    logging.info("🔧 Exporting to ONNX (.onnx)... This may take a minute.")
    model.export(format='onnx', device=0, half=True, dynamic=False)
    
    logging.info("✅ Export complete! You should now see 'yolo26m.onnx' in your folder.")

if __name__ == '__main__':
    export_to_onnx()
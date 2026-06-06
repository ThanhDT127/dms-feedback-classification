import sys
from pathlib import Path
from PIL import Image

def main():
    service_dir = Path(__file__).resolve().parent.parent
    src_img = service_dir / "asset chatbot" / "Nen-chatbot.png"
    dest_img = service_dir / "static" / "assets" / "background.png"
    dest_jpg = service_dir / "static" / "assets" / "background.jpg"
    
    print(f"Compressing {src_img} -> {dest_img}...")
    
    if not src_img.exists():
        print(f"Error: source image {src_img} not found.")
        sys.exit(1)
        
    dest_img.parent.mkdir(parents=True, exist_ok=True)
    
    with Image.open(src_img) as img:
        width, height = img.size
        print(f"Original size: {width}x{height}")
        
        # Limit max width to 1920px to reduce size
        if width > 1920:
            ratio = 1920 / width
            new_size = (1920, int(height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            print(f"Resized to: {img.size}")
            
        # 1. Save as JPEG (extremely small size, ideal for backgrounds)
        img_rgb = img.convert("RGB")
        img_rgb.save(dest_jpg, "JPEG", quality=85, optimize=True)
        jpg_size = dest_jpg.stat().st_size
        print(f"Saved optimized JPEG to {dest_jpg} ({jpg_size / 1024:.1f} KB)")
        
        # 2. Save as PNG (fallback/lossless)
        img.save(dest_img, "PNG", optimize=True)
        png_size = dest_img.stat().st_size
        print(f"Saved optimized PNG to {dest_img} ({png_size / 1024:.1f} KB)")

if __name__ == "__main__":
    main()

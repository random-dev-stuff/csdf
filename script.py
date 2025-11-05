import os 
import sys 
import json 
import hashlib 
import subprocess 
import tempfile 
import shutil 
import glob 
from datetime import datetime 
from pathlib import Path 
from PIL import Image 
import imagehash 
import cv2 
from skimage.metrics import structural_similarity as ssim 
from tkinter import Tk 
from tkinter.filedialog import askopenfilename 
 
def sha256_file(path):
    """Compute SHA-256 hash of a file with proper error handling."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    
    if not os.path.isfile(path):
        raise ValueError(f"Path is not a file: {path}")
    
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except PermissionError:
        raise PermissionError(f"Permission denied accessing file: {path}")
    except Exception as e:
        raise RuntimeError(f"Error reading file {path}: {str(e)}")
    
    return h.hexdigest() 
def run_ffprobe(path): 
    """Return ffprobe JSON (requires ffprobe/ffmpeg installed).""" 
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", 
path] 
    res = subprocess.run(cmd, capture_output=True, text=True) 
    if res.returncode != 0: 
        raise RuntimeError(f"ffprobe failed: {res.stderr.strip()}") 
    return json.loads(res.stdout) 
 
def extract_frames(video_path, out_dir, skip=0): 
    """ 
    Use ffmpeg to extract frames into out_dir. 
    skip=0 -> extract every frame 
    skip=n (n>1) -> extract every n-th frame 
    """ 
    os.makedirs(out_dir, exist_ok=True) 
    if skip and skip > 1: 
        # select every n-th frame 
        vf = f"select=not(mod(n\\,{skip}))" 
        cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-vsync", "vfr", os.path.join(out_dir, 
"frame-%06d.jpg")] 
    else: 
        cmd = ["ffmpeg", "-y", "-i", video_path, "-q:v", "2", os.path.join(out_dir, "frame-%06d.jpg")] 
    res = subprocess.run(cmd, capture_output=True, text=True) 
    if res.returncode != 0: 
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()}") 
    # return list of saved frame paths 
    return sorted(glob.glob(os.path.join(out_dir, "frame-*.jpg"))) 
 
def index_frames(frame_files, out_csv_path): 
    """Compute pHash and per-frame sha256, write simple CSV-like JSON.""" 
    index = [] 
    for f in frame_files: 
        try: 
            ph = str(imagehash.phash(Image.open(f))) 
        except Exception as e: 
            ph = "" 
        h = sha256_file(f) 
        index.append({"frame": os.path.basename(f), "path": f, "phash": ph, "sha256": h}) 
    with open(out_csv_path, "w", encoding="utf-8") as j: 
        json.dump(index, j, indent=2) 
    return index 
 
def temporal_ssim(frame_files, threshold=0.60): 
    """Compute SSIM between consecutive frames. Return scores and flag low SSIM 
pairs.""" 
    scores = [] 
    anomalies = [] 
    for i in range(len(frame_files) - 1): 
        a = cv2.imread(frame_files[i], cv2.IMREAD_GRAYSCALE) 
        b = cv2.imread(frame_files[i+1], cv2.IMREAD_GRAYSCALE) 
        if a is None or b is None: 
            continue 
        try:
            s = ssim(a, b)
        except Exception:
            s = 0.0
        item = {"frame": os.path.basename(frame_files[i]), "next_frame": 
               os.path.basename(frame_files[i+1]), "ssim": float(s)}
        scores.append(item)
        if s < threshold:
            anomalies.append(item)
    return scores, anomalies 
def render_html_report(out_html, video_name, sha, metadata, index, anomalies, 
                       results_folder):
    """Simple HTML report."""
    now = datetime.utcnow().isoformat()
    html = f"""<!doctype html> 
<html> 
<head> 
<meta charset="utf-8"> 
<title>Video Forensic Report - {video_name}</title> 
<style> 
body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 2rem; }} 
pre {{ background:#f4f4f4; padding:1rem; overflow:auto; }} 
.anomaly {{ color: darkred; }} 
.thumb {{ max-width: 240px; border:1px solid #ccc; padding:4px; margin:6px; }} 
</style> 
</head> 
<body> 
<h1>Video Forensic Report</h1> 
<p><strong>Video:</strong> {video_name}</p> 
<p><strong>SHA-256:</strong> {sha}</p> 
<p><strong>Generated (UTC):</strong> {now}</p> 
  <h2>ffprobe metadata</h2>
  <pre>{json.dumps(metadata, indent=2)}</pre>

  <h2>Flagged temporal anomalies (low SSIM)</h2>
  <p>Threshold used: SSIM &lt; 0.60</p>
"""
    if anomalies: 
        html += "<ul>\n"
        for a in anomalies:
            f1 = os.path.join("frames", a["frame"])
            f2 = os.path.join("frames", a["next_frame"]) 
            html += f'<li class="anomaly">{a["frame"]} -> {a["next_frame"]} (ssim={a["ssim"]:.3f})<br>'
            
            if os.path.exists(os.path.join(results_folder, "frames", a["frame"])):
                html += f'<img class="thumb" src="frames/{a["frame"]}"> <img class="thumb" src="frames/{a["next_frame"]}">' 
            html += "</li>\n"
        html += "</ul>\n"
    else:
        html += "<p>No low-SSIM anomalies detected.</p>" 
 
    html += """
  <h2>Frame index (first 20 entries)</h2>
  <pre>
"""
    # add first 20 index entries
    for entry in index[:20]:
        html += json.dumps(entry) + "\n"
    html += "</pre>\n"

    html += "</body></html>"

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html) 
 

def forensic_pipeline(video_path=None, skip=0, output_root="results", cleanup=False): 
    # Determine video_path (picker if None) 
    if not video_path: 
        Tk().withdraw() 
        video_path = askopenfilename(title="Select a video file", filetypes=[("Video files", 
".mp4 *.mov *.avi *.mkv *.webm"), ("All files", ".*")]) 
        if not video_path: 
            print("No file selected. Exiting.") 
            return 

    video_path = str(Path(video_path).resolve()) 
    
    # Validate video file exists and is accessible
    print(f"Video: {video_path}")
    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        print("Please check the file path and try again.")
        return
    
    if not os.path.isfile(video_path):
        print(f"ERROR: Path is not a file: {video_path}")
        return
    
    os.makedirs(output_root, exist_ok=True) 
    frames_dir = os.path.join(output_root, "frames") 

    print("Computing SHA-256...") 
    try:
        sha = sha256_file(video_path)
    except Exception as e:
        print(f"ERROR: Failed to compute SHA-256: {e}")
        return
    print("SHA-256:", sha) 
 
    print("Extracting ffprobe metadata...") 
    try: 
        meta = run_ffprobe(video_path) 
    except Exception as e: 
        print("ffprobe error:", e) 
        meta = {} 
 
    # extract frames 
    print("Extracting frames with ffmpeg (this may take a while)...") 
    try: 
        frame_files = extract_frames(video_path, frames_dir, skip) 
    except Exception as e: 
        print("ffmpeg extraction failed:", e) 
        return 
 
    print(f"Extracted {len(frame_files)} frames to {frames_dir}") 
 
    # index frames 
    print("Indexing frames (pHash + per-frame SHA256)...") 
    index_path = os.path.join(output_root, "frame_index.json") 
    index = index_frames(frame_files, index_path) 
    print("Wrote", index_path) 
 
    # temporal SSIM 
    print("Computing SSIM between consecutive frames...") 
    scores, anomalies = temporal_ssim(frame_files, threshold=0.60) 
    with open(os.path.join(output_root, "temporal.json"), "w", encoding="utf-8") as f: 
        json.dump(scores, f, indent=2) 
    print("Temporal analysis saved to", os.path.join(output_root, "temporal.json")) 
 
    # generate HTML report
    print("Rendering HTML report...")
    out_html = os.path.join(output_root, "report.html")
    render_html_report(out_html, os.path.basename(video_path), sha, meta, index,
                      anomalies, output_root)
    print("Report written to", out_html)
    
    # optional cleanup
    if cleanup:
        print("Cleaning up extracted frames...")
        shutil.rmtree(frames_dir, ignore_errors=True)
    
    print("Pipeline finished.")

# Main execution
if __name__ == "__main__":
    
    video_path = None  
    
    
    
    forensic_pipeline(video_path=video_path, skip=5, output_root="results", 
                     cleanup=False) 
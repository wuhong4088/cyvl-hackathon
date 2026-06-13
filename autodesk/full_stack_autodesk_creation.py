import os
import time
import base64
import requests
import urllib.parse
import http.server
import socketserver
import webbrowser
import numpy as np
import laspy
import open3d as o3d

# --- 1. Configuration ---
APS_CLIENT_ID = os.environ.get("APS_CLIENT_ID", "your_client_id_here")
APS_CLIENT_SECRET = os.environ.get("APS_CLIENT_SECRET", "your_client_secret_here")

# File definitions - automatically resolve path relative to project root or script location
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)

laz_candidate_1 = os.path.join(project_root, "data", "download", "global_xyz_rgb_icgu_76_12000_14000.laz")
laz_candidate_2 = os.path.join(script_dir, "global_xyz_rgb_icgu_76_12000_14000.laz")

if os.path.exists(laz_candidate_1):
    INPUT_LAZ = laz_candidate_1
else:
    INPUT_LAZ = laz_candidate_2

OUTPUT_OBJ = os.path.join(script_dir, "global_xyz_rgb_icgu_76_12000_14000.obj")

FILE_NAME = os.path.basename(OUTPUT_OBJ)
BUCKET_KEY = f"cyvl_hackathon_bucket_{APS_CLIENT_ID.lower()}"


def generate_obj_from_laz(laz_path, obj_path, sample_stride=50):
    """Task 0: Convert LAZ to a smooth 3D OBJ using Voxel-Optimized Poisson"""
    print(f"\n--- STEP 0: Point Cloud Meshing ---")
    start_time = time.time()
    print("Reading LAZ file...")

    with laspy.open(laz_path) as fh:
        las = fh.read()

    # 1. Initial RAM-saving slice (prevents memory crashes before Open3D even starts)
    sampled_points = las.points[::sample_stride]

    x = np.array(sampled_points.x)
    y = np.array(sampled_points.y)
    z = np.array(sampled_points.z)
    points_3d = np.vstack((x, y, z)).T

    # Center the point cloud to 0,0,0
    center = np.mean(points_3d, axis=0)
    points_3d -= center

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points_3d)

    # 2. The Magic Fix: Voxel Downsampling
    print(f"Points before voxelation: {len(pcd.points):,}")

    # We dynamically increase the voxel cube size until the point count
    # drops below 150,000, which guarantees a fast generation time.
    voxel_size = 0.5
    downpcd = pcd.voxel_down_sample(voxel_size=voxel_size)

    while len(downpcd.points) > 150000:
        voxel_size += 0.2
        downpcd = pcd.voxel_down_sample(voxel_size=voxel_size)

    print(
        f"Points after voxelation: {len(downpcd.points):,} (Voxel size used: {voxel_size:.1f})"
    )
    print(f"Elapsed time: {time.time() - start_time:.1f}s")

    print("\nEstimating 3D normals...")
    down_start = time.time()
    downpcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamKNN(knn=20))
    downpcd.orient_normals_consistent_tangent_plane(100)
    print(f"Normals done. ({time.time() - down_start:.1f}s)")

    print("\nRunning Poisson Surface Reconstruction (Depth 8)...")
    poisson_start = time.time()
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        downpcd, depth=8
    )
    print(f"Reconstruction done. ({time.time() - poisson_start:.1f}s)")

    print("\nCleaning up low-density artifacts...")
    densities = np.asarray(densities)
    vertices_to_remove = densities < np.quantile(densities, 0.05)
    mesh.remove_vertices_by_mask(vertices_to_remove)

    print(f"Exporting clean 3D mesh to {obj_path}...")
    o3d.io.write_triangle_mesh(obj_path, mesh)
    print(f"Meshing complete! Total Time: {time.time() - start_time:.1f}s")

    return obj_path


def get_access_token():
    print("\n--- STEP 1: Obtaining Autodesk Access Token ---")
    url = "https://developer.api.autodesk.com/authentication/v2/token"
    credentials = f"{APS_CLIENT_ID}:{APS_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "bucket:create bucket:read data:create data:write data:read",
    }

    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response.json()["access_token"]


def create_bucket(token):
    print(f"Ensuring OSS Bucket '{BUCKET_KEY}' exists...")
    url = "https://developer.api.autodesk.com/oss/v2/buckets"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"bucketKey": BUCKET_KEY, "policyKey": "transient"}

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 409:
        response.raise_for_status()


def upload_file(token, file_path, file_name):
    print(f"\n--- STEP 2: Uploading {file_name} to AWS S3 ---")
    safe_file_name = urllib.parse.quote(file_name)
    s3_url_endpoint = f"https://developer.api.autodesk.com/oss/v2/buckets/{BUCKET_KEY}/objects/{safe_file_name}/signeds3upload?minutesExpiration=15"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    response = requests.get(s3_url_endpoint, headers=headers)
    response.raise_for_status()
    s3_data = response.json()

    print(" -> Streaming binary data...")
    with open(file_path, "rb") as f:
        requests.put(s3_data["urls"][0], data=f).raise_for_status()

    print(" -> Finalizing upload with Autodesk...")
    finalize_url = f"https://developer.api.autodesk.com/oss/v2/buckets/{BUCKET_KEY}/objects/{safe_file_name}/signeds3upload"
    final_response = requests.post(
        finalize_url, headers=headers, json={"uploadKey": s3_data["uploadKey"]}
    )
    final_response.raise_for_status()

    object_id = final_response.json()["objectId"]
    urn = base64.urlsafe_b64encode(object_id.encode()).decode().rstrip("=")
    return urn


def translate_file(token, urn):
    print(f"\n--- STEP 3: Translating OBJ to SVF2 (Cloud Rendering) ---")
    url = "https://developer.api.autodesk.com/modelderivative/v2/designdata/job"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "input": {"urn": urn},
        "output": {"formats": [{"type": "svf2", "views": ["2d", "3d"]}]},
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code not in [200, 201]:
        response.raise_for_status()


def check_translation_status(token, urn):
    print("Polling translation status...")
    url = f"https://developer.api.autodesk.com/modelderivative/v2/designdata/{urn}/manifest"
    headers = {"Authorization": f"Bearer {token}"}

    while True:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        manifest = response.json()

        status = manifest.get("status", "unknown")
        print(f" -> Status: {status} ({manifest.get('progress', 'unknown')})")

        if status == "success":
            print("✅ Translation Complete!")
            return True
        elif status in ["failed", "timeout"]:
            print("❌ Translation failed.")
            return False

        time.sleep(5)


def generate_html_file(token, urn):
    print("\n--- STEP 4: Generating Local HTML ---")
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hackathon 3D Viewer</title>
    <link rel="stylesheet" href="https://developer.api.autodesk.com/modelderivative/v2/viewers/7.*/style.min.css" type="text/css">
    <script src="https://developer.api.autodesk.com/modelderivative/v2/viewers/7.*/viewer3D.min.js"></script>
    <style>
        body {{ margin: 0; padding: 0; overflow: hidden; background-color: #1a1a1a; }}
        #forgeViewer {{ width: 100vw; height: 100vh; position: relative; }}
    </style>
</head>
<body>
    <div id="forgeViewer"></div>
    <script>
        var viewer;
        var documentId = 'urn:{urn}'; 
        var accessToken = '{token}';

        var options = {{
            env: 'AutodeskProduction2',
            api: 'streamingV2',
            getAccessToken: function(onTokenReady) {{
                onTokenReady(accessToken, 3600);
            }}
        }};

        Autodesk.Viewing.Initializer(options, function() {{
            viewer = new Autodesk.Viewing.GuiViewer3D(document.getElementById('forgeViewer'));
            viewer.start();
            Autodesk.Viewing.Document.load(documentId, function(doc) {{
                var viewables = doc.getRoot().getDefaultGeometry();
                viewer.loadDocumentNode(doc, viewables);
            }}, function(err) {{
                console.error('Load Error:', err);
            }});
        }});
    </script>
</body>
</html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)


def start_local_server(port=8000):
    print("\n--- STEP 5: Launching Viewer ---")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(
        ("", port), http.server.SimpleHTTPRequestHandler
    ) as httpd:
        url = f"http://localhost:{port}"
        print(f"🚀 Server running at: {url}")
        print("Press Ctrl+C to close.")
        webbrowser.open(url)
        httpd.serve_forever()


if __name__ == "__main__":
    try:
        # Pipeline Execution
        generate_obj_from_laz(INPUT_LAZ, OUTPUT_OBJ, sample_stride=200)

        token = get_access_token()
        create_bucket(token)
        document_urn = upload_file(token, OUTPUT_OBJ, FILE_NAME)
        translate_file(token, document_urn)

        if check_translation_status(token, document_urn):
            generate_html_file(token, document_urn)
            start_local_server(port=8000)

    except Exception as e:
        print(f"\nPipeline Error: {str(e)}")

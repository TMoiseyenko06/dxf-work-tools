import io
import os
import tempfile
import uuid

from flask import Flask, jsonify, render_template, request, send_file

from dxf_processor import process_dxf

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB

# In-memory store: result_id -> temp file path
_results: dict[str, str] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/process", methods=["POST"])
def api_process():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not file.filename.lower().endswith(".dxf"):
        return jsonify({"error": "File must have a .dxf extension"}), 400

    try:
        x = float(request.form.get("inline_amount", 1.0))
        y = float(request.form.get("outline_amount", 2.0))
        z = float(request.form.get("inline_outline_amount", 1.0))
    except (TypeError, ValueError):
        return jsonify({"error": "Parameters must be valid numbers"}), 400

    if x <= 0 or y <= 0 or z <= 0:
        return jsonify({"error": "All offset amounts must be positive"}), 400

    input_fd, input_path = tempfile.mkstemp(suffix=".dxf")
    output_path = input_path + "_out.dxf"

    try:
        file.save(input_path)
        stats = process_dxf(input_path, output_path, x, y, z)

        result_id = str(uuid.uuid4())
        _results[result_id] = output_path

        return jsonify({"success": True, "result_id": result_id, "stats": stats})

    except ValueError as e:
        if os.path.exists(output_path):
            os.unlink(output_path)
        return jsonify({"error": str(e)}), 422

    except Exception as e:
        if os.path.exists(output_path):
            os.unlink(output_path)
        return jsonify({"error": f"Processing failed: {e}"}), 500

    finally:
        os.close(input_fd)
        if os.path.exists(input_path):
            os.unlink(input_path)


@app.route("/api/download/<result_id>")
def api_download(result_id):
    path = _results.get(result_id)
    if not path or not os.path.exists(path):
        return jsonify({"error": "Result not found or already downloaded"}), 404

    # Read into memory so we can clean up the temp file immediately
    with open(path, "rb") as f:
        data = f.read()

    os.unlink(path)
    _results.pop(result_id, None)

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="processed.dxf",
        mimetype="application/octet-stream",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)


import os
import re
import uuid
import html
import bleach
import mysql.connector
from pathlib import Path
from ai_notes import generate_study_notes
from bleach.css_sanitizer import CSSSanitizer
from flask import jsonify
from flask import send_from_directory
from werkzeug.utils import secure_filename
from file_processor import extract_text
from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_user,
    logout_user,
    login_required,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_db_connection


load_dotenv()

app = Flask(__name__)

secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY is missing from your .env file.")

app.config["SECRET_KEY"] = secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Enable this when the app is deployed over HTTPS.
app.config["SESSION_COOKIE_SECURE"] = (
    os.getenv("COOKIE_SECURE", "false").lower() == "true"
)

#upload settings

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")

ALLOWED_EXTENSIONS = {
    "pdf", "txt", "docx", "pptx", "xlsx", "csv"
}

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access that page."
login_manager.login_message_category = "info"


class User(UserMixin):
    def __init__(self, user_id, username, email, password_hash):
        self.id = user_id
        self.username = username
        self.email = email
        self.password_hash = password_hash


def find_user_by_id(user_id):
    connection = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, username, email, password_hash
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        )

        row = cursor.fetchone()
        cursor.close()

        if row is None:
            return None

        return User(
            row["id"],
            row["username"],
            row["email"],
            row["password_hash"],
        )

    finally:
        if connection is not None and connection.is_connected():
            connection.close()


@login_manager.user_loader
def load_user(user_id):
    try:
        return find_user_by_id(int(user_id))
    except (ValueError, TypeError):
        return None


@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please fill in all required fields.", "error")
            return render_template("register.html")

        if len(username) > 50:
            flash("Username must be 50 characters or fewer.", "error")
            return render_template("register.html")

        if len(email) > 255 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            flash("Please enter a valid email address.", "error")
            return render_template("register.html")

        if len(password) < 8:
            flash("Your password must be at least 8 characters.", "error")
            return render_template("register.html")

        if len(password.encode("utf-8")) > 1024:
            flash("Password is too long.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("The passwords do not match.", "error")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        connection = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (username, email, password_hash),
            )

            connection.commit()
            cursor.close()

            flash("Account created successfully. You can now log in.", "success")
            return redirect(url_for("login"))

        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()

            flash(
                "We could not create that account. The email may already be registered.",
                "error",
            )

        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()

            app.logger.exception("Database error during registration.")
            flash("Unable to create your account right now.", "error")

        finally:
            if connection is not None and connection.is_connected():
                connection.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT id, username, email, password_hash
                FROM users
                WHERE email = %s
                """,
                (email,),
            )

            row = cursor.fetchone()
            cursor.close()
            connection.close()

            if row:
                user = User(
                    row["id"],
                    row["username"],
                    row["email"],
                    row["password_hash"],
                )

        except mysql.connector.Error:
            app.logger.exception("Database error during login.")
            flash("Unable to log in right now. Please try again.", "error")
            return render_template("login.html")

        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=False)
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have logged out.", "success")
    return redirect(url_for("home"))



@app.route("/dashboard")
@login_required
def dashboard():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, title, original_filename, file_type,
                   uploaded_at, last_opened_at
            FROM documents
            WHERE user_id = %s
            ORDER BY uploaded_at DESC
            """,
            (current_user.id,),
        )

        documents = cursor.fetchall()

        return render_template(
            "dashboard.html",
            documents=documents
        )

    except mysql.connector.Error:
        app.logger.exception("Could not load saved documents.")
        flash("Could not load your saved documents.", "error")
        return render_template("dashboard.html", documents=[])

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()



@app.route("/upload", methods=["POST"])
@login_required
def upload_document():
    if "document" not in request.files:
        flash("Please choose a file first.", "error")
        return redirect(url_for("dashboard"))

    uploaded_file = request.files["document"]

    if not uploaded_file.filename:
        flash("Please choose a file first.", "error")
        return redirect(url_for("dashboard"))

    original_filename = secure_filename(uploaded_file.filename)

    if not original_filename:
        flash("The filename is invalid.", "error")
        return redirect(url_for("dashboard"))

    file_type = Path(original_filename).suffix.lower().lstrip(".")

    if file_type not in ALLOWED_EXTENSIONS:
        flash("That file type is not supported.", "error")
        return redirect(url_for("dashboard"))

    stored_filename = f"{uuid.uuid4().hex}.{file_type}"
    file_path = os.path.join(UPLOAD_FOLDER, stored_filename)

    connection = None
    cursor = None

    try:
        uploaded_file.save(file_path)

        extracted_text = extract_text(file_path, file_type)

        # Use the original filename as the initial editable title.
        initial_title = original_filename

        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO documents
                (user_id, original_filename, stored_filename,
                 file_type, status, title, extracted_text)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                current_user.id,
                original_filename,
                stored_filename,
                file_type,
                "uploaded",
                initial_title,
                extracted_text,
            ),
        )

        document_id = cursor.lastrowid
        connection.commit()

        flash("Your document and extracted text have been saved.", "success")

        return redirect(
            url_for("view_document", document_id=document_id)
        )

    except ValueError as error:
        if connection is not None:
            connection.rollback()

        if os.path.exists(file_path):
            os.remove(file_path)

        flash(str(error), "error")
        return redirect(url_for("dashboard"))

    except Exception:
        if connection is not None:
            connection.rollback()

        if os.path.exists(file_path):
            os.remove(file_path)

        app.logger.exception("Document upload failed.")
        flash("The document could not be saved. Please try again.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()




@app.route("/document/<int:document_id>")
@login_required
def view_document(document_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT id, title, original_filename, file_type,
                   extracted_text, editor_content,
                   uploaded_at, last_opened_at
            FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, current_user.id),
        )

        document = cursor.fetchone()

        if document is None:
            flash("Document not found.", "error")
            return redirect(url_for("dashboard"))

        cursor.execute(
            """
            UPDATE documents
            SET last_opened_at = CURRENT_TIMESTAMP
            WHERE id = %s AND user_id = %s
            """,
            (document_id, current_user.id),
        )
        connection.commit()

        # Use the saved editor version if available.
        # Otherwise, safely prepare the original extracted text.
        if document["editor_content"] is not None:
            editor_content = document["editor_content"]
        else:
            original_text = document["extracted_text"] or ""
            editor_content = html.escape(original_text).replace(
                "\n", "<br>"
            )
            
        cursor.execute("""
            SELECT title, content, updated_at
            FROM notes
            WHERE document_id = %s AND user_id = %s
        """, (document_id, current_user.id))

        generated_notes = cursor.fetchone()

        return render_template(
            "preview.html",
            document=document,
            editor_content=editor_content,
            generated_notes=generated_notes
        )

    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()

        app.logger.exception("Could not open saved document.")
        flash("Could not open the document.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/document/<int:document_id>/rename", methods=["POST"])
@login_required
def rename_document(document_id):
    new_title = request.form.get("title", "").strip()

    if not new_title:
        flash("The document title cannot be empty.", "error")
        return redirect(
            url_for("view_document", document_id=document_id)
        )

    if len(new_title) > 255:
        flash("The title must be 255 characters or fewer.", "error")
        return redirect(
            url_for("view_document", document_id=document_id)
        )

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE documents
            SET title = %s
            WHERE id = %s AND user_id = %s
            """,
            (new_title, document_id, current_user.id),
        )

        if cursor.rowcount == 0:
            connection.rollback()
            flash("Document not found or title unchanged.", "error")
            return redirect(url_for("dashboard"))

        connection.commit()
        flash("Document title updated successfully.", "success")

        return redirect(
            url_for("view_document", document_id=document_id)
        )

    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()

        app.logger.exception("Could not rename document.")
        flash("Could not rename the document.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


@app.route("/document/<int:document_id>/save", methods=["POST"])
@login_required
def save_document_edits(document_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "Invalid request."}), 400

    title = data.get("title")
    content = data.get("content")

    if not isinstance(title, str) or not isinstance(content, str):
        return jsonify({"success": False, "message": "Invalid document data."}), 400

    title = title.strip()

    if not title:
        return jsonify({"success": False, "message": "Title cannot be empty."}), 400

    if len(title) > 255:
        return jsonify({"success": False, "message": "Title is too long."}), 400

    if len(content) > 1_000_000:
        return jsonify({"success": False, "message": "Document is too large to save."}), 413

    allowed_tags = [
        "p", "br", "strong", "b", "em", "i", "u",
        "mark", "span", "div", "ul", "ol", "li",
        "font", "sub", "sup", "blockquote"
    ]

    allowed_attributes = {
        "span": ["style"],
        "font": ["face", "size"],
    }

    css_sanitizer = CSSSanitizer(
        allowed_css_properties=[
            "background-color",
            "color",
            "font-family",
            "font-size",
            "text-align",
        ]
    )

    safe_content = bleach.clean(
        content,
        tags=allowed_tags,
        attributes=allowed_attributes,
        css_sanitizer=css_sanitizer,
        strip=True,
    )

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Check ownership before saving anything.
        cursor.execute(
            """
            SELECT id FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, current_user.id),
        )

        if cursor.fetchone() is None:
            return jsonify({
                "success": False,
                "message": "Document not found."
            }), 404

        cursor.execute(
            """
            UPDATE documents
            SET title = %s, editor_content = %s
            WHERE id = %s AND user_id = %s
            """,
            (title, safe_content, document_id, current_user.id),
        )

        connection.commit()

        return jsonify({
            "success": True,
            "message": "All changes saved."
        })

    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()

        app.logger.exception("Could not save document edits.")

        return jsonify({
            "success": False,
            "message": "Could not save. Please try again."
        }), 500

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()



@app.route("/document/<int:document_id>/delete", methods=["POST"])
@login_required
def delete_document(document_id):
    connection = None
    cursor = None
    stored_filename = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Only find documents owned by the logged-in user.
        cursor.execute(
            """
            SELECT stored_filename
            FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, current_user.id),
        )

        document = cursor.fetchone()

        if document is None:
            flash("Document not found.", "error")
            return redirect(url_for("dashboard"))

        stored_filename = document["stored_filename"]

        cursor.execute(
            """
            DELETE FROM documents
            WHERE id = %s AND user_id = %s
            """,
            (document_id, current_user.id),
        )

        connection.commit()

    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()

        app.logger.exception("Could not delete document.")
        flash("Could not delete the document. Please try again.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()

    # Remove the physical file only after the database deletion succeeds.
    if stored_filename:
        safe_filename = os.path.basename(stored_filename)
        file_path = os.path.join(UPLOAD_FOLDER, safe_filename)

        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            app.logger.exception("Could not remove uploaded file.")

    flash("Document deleted successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/documents/delete", methods=["POST"])
@login_required
def delete_selected_documents():
    raw_ids = request.form.getlist("document_ids")
    document_ids = []

    for value in raw_ids:
        try:
            document_id = int(value)

            if document_id > 0 and document_id not in document_ids:
                document_ids.append(document_id)
        except (ValueError, TypeError):
            continue

    if not document_ids:
        flash("Select at least one document to delete.", "error")
        return redirect(url_for("dashboard"))

    connection = None
    cursor = None
    stored_filenames = []

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        placeholders = ", ".join(["%s"] * len(document_ids))

        # Retrieve only files owned by the current user.
        cursor.execute(
            f"""
            SELECT id, stored_filename
            FROM documents
            WHERE user_id = %s
              AND id IN ({placeholders})
            """,
            (current_user.id, *document_ids),
        )

        owned_documents = cursor.fetchall()

        if not owned_documents:
            flash("No selected documents were found.", "error")
            return redirect(url_for("dashboard"))

        owned_ids = [document["id"] for document in owned_documents]
        stored_filenames = [
            document["stored_filename"]
            for document in owned_documents
        ]

        owned_placeholders = ", ".join(["%s"] * len(owned_ids))

        cursor.execute(
            f"""
            DELETE FROM documents
            WHERE user_id = %s
              AND id IN ({owned_placeholders})
            """,
            (current_user.id, *owned_ids),
        )

        connection.commit()

    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()

        app.logger.exception("Could not delete selected documents.")
        flash("Could not delete the selected documents.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()

    deleted_count = len(owned_ids)

    for stored_filename in stored_filenames:
        safe_filename = os.path.basename(stored_filename)
        file_path = os.path.join(UPLOAD_FOLDER, safe_filename)

        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            app.logger.exception("Could not remove uploaded file.")

    flash(
        f"{deleted_count} document(s) deleted successfully.",
        "success",
    )
    return redirect(url_for("dashboard"))



@app.route("/document/<int:document_id>/generate-notes", methods=["POST"])
@login_required
def generate_document_notes(document_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT id, title, extracted_text
            FROM documents
            WHERE id = %s AND user_id = %s
        """, (document_id, current_user.id))

        document = cursor.fetchone()

        if document is None:
            flash("Document not found.", "error")
            return redirect(url_for("dashboard"))

        document_title = document["title"]
        extracted_text = document["extracted_text"] or ""

    except mysql.connector.Error:
        app.logger.exception("Could not retrieve the source document.")
        flash("Could not open the source document.", "error")
        return redirect(url_for("dashboard"))

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    if not extracted_text.strip():
        flash("This document has no readable text.", "error")
        return redirect(url_for("view_document", document_id=document_id))

    try:
        generated_content = generate_study_notes(
            document_title,
            extracted_text
        )

        allowed_tags = [
            "h2", "h3", "p", "br", "strong", "b", "em", "i",
            "u", "ul", "ol", "li", "blockquote"
        ]

        safe_content = bleach.clean(
            generated_content,
            tags=allowed_tags,
            attributes={},
            strip=True
        )

        if not safe_content.strip():
            raise ValueError("No usable notes were generated.")

        # Keep the new file unsaved until the user clicks Save Notes.
        return render_template(
            "ai_note_editor.html",
            document={
                "title": (document_title + " - AI Notes")[:255],
                "source_document_id": document_id,
            },
            editor_content=safe_content,
            save_url=url_for(
                "save_ai_note",
                document_id=document_id
            ),
            is_saved=False
        )

    except Exception:
        app.logger.exception("Could not generate AI notes.")
        flash("Could not generate AI notes. Please try again.", "error")
        return redirect(url_for("view_document", document_id=document_id))


@app.route("/document/<int:document_id>/save-ai-note", methods=["POST"])
@login_required
def save_ai_note(document_id):
    connection = None
    cursor = None
    stored_filename = None

    try:
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        content = data.get("content")

        if not isinstance(title, str) or not title.strip():
            return jsonify({
                "success": False,
                "error": "Please enter a note title."
            }), 400

        if len(title.strip()) > 255:
            return jsonify({
                "success": False,
                "error": "The title must be 255 characters or fewer."
            }), 400

        if not isinstance(content, str) or not content.strip():
            return jsonify({
                "success": False,
                "error": "The note content cannot be empty."
            }), 400

        if len(content) > 1_000_000:
            return jsonify({
                "success": False,
                "error": "The notes are too large to save."
            }), 400

        allowed_tags = [
            "h2", "h3", "p", "br", "strong", "b", "em", "i",
            "u", "ul", "ol", "li", "blockquote"
        ]

        safe_content = bleach.clean(
            content,
            tags=allowed_tags,
            attributes={},
            strip=True
        )

        if not safe_content.strip():
            return jsonify({
                "success": False,
                "error": "No usable note content was found."
            }), 400

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Verify that the source document belongs to this user.
        cursor.execute("""
            SELECT id
            FROM documents
            WHERE id = %s AND user_id = %s
        """, (document_id, current_user.id))

        if cursor.fetchone() is None:
            return jsonify({
                "success": False,
                "error": "Source document not found."
            }), 404

        stored_filename = uuid.uuid4().hex + ".html"
        original_filename = (
            secure_filename(title.strip()) or "AI_Study_Notes"
        ) + ".html"

        file_path = os.path.join(UPLOAD_FOLDER, stored_filename)

        # Store the generated notes as their own physical file.
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(safe_content)

        try:
            cursor.execute("""
                INSERT INTO documents (
                    user_id,
                    original_filename,
                    stored_filename,
                    file_type,
                    status,
                    title,
                    extracted_text,
                    editor_content,
                    document_kind,
                    source_document_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                current_user.id,
                original_filename,
                stored_filename,
                "html",
                "completed",
                title.strip(),
                "",
                safe_content,
                "ai_note",
                document_id
            ))

            new_document_id = cursor.lastrowid
            connection.commit()

        except Exception:
            connection.rollback()
            if os.path.isfile(file_path):
                os.remove(file_path)
            raise

        return jsonify({
            "success": True,
            "redirect_url": url_for(
                "view_document",
                document_id=new_document_id
            )
        })

    except Exception:
        app.logger.exception("Could not save AI notes.")

        if connection is not None:
            connection.rollback()

        return jsonify({
            "success": False,
            "error": "Could not save notes. Please try again."
        }), 500

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()



if __name__ == "__main__":
    app.run(debug=True)


from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import os
import re
import requests

from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load secret values from .env
load_dotenv()

# TeamTrace GitHub Integration
app = Flask(__name__)
app.secret_key = "teamtrace_mvp_secret"

DATABASE = os.path.join(os.path.dirname(__file__), "teamtrace.db")

GITHUB_API = "https://api.github.com"


# DATABASE CONNECTION
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# CREATE DATABASE TABLES
def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (member_id) REFERENCES members(id)
        )
    """)

    # Store GitHub commits
    conn.execute("""
        CREATE TABLE IF NOT EXISTS github_commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sha TEXT NOT NULL UNIQUE,
            author TEXT,
            author_login TEXT,
            message TEXT,
            committed_at TEXT,
            html_url TEXT
        )
    """)

    conn.commit()
    conn.close()


# MAIN DASHBOARD
@app.route("/")
def index():
    conn = get_db()

    members = conn.execute("""
        SELECT * FROM members ORDER BY name
    """).fetchall()

    contributions = conn.execute("""
        SELECT contributions.*, members.name
        FROM contributions
        JOIN members ON contributions.member_id = members.id
        ORDER BY contributions.id DESC
        LIMIT 50
    """).fetchall()

    member_counts = conn.execute("""
        SELECT members.name, COUNT(contributions.id) AS total
        FROM members
        LEFT JOIN contributions
        ON members.id = contributions.member_id
        GROUP BY members.id
        ORDER BY total DESC
    """).fetchall()

    total_members = len(members)

    total_contributions = conn.execute("""
        SELECT COUNT(*) FROM contributions
    """).fetchone()[0]

    # ACTIVITY OVER THE LAST 7 DAYS
    today = datetime.now().date()
    start_date = today - timedelta(days=6)

    daily_data = conn.execute("""
        SELECT date(created_at) AS day, COUNT(*) AS total
        FROM contributions
        WHERE date(created_at) >= ?
        GROUP BY date(created_at)
    """, (start_date.isoformat(),)).fetchall()

    daily_map = {row["day"]: row["total"] for row in daily_data}

    date_labels = []
    daily_counts = []

    for i in range(7):
        current_date = start_date + timedelta(days=i)
        date_labels.append(current_date.strftime("%d %b"))
        daily_counts.append(
            daily_map.get(current_date.isoformat(), 0)
        )

    # GET SAVED GITHUB COMMITS
    github_commits = conn.execute("""
        SELECT * FROM github_commits
        ORDER BY committed_at DESC
        LIMIT 20
    """).fetchall()

    # GITHUB CONTRIBUTION SUMMARY
    github_member_counts = conn.execute("""
        SELECT
            COALESCE(NULLIF(author_login, ''), author, 'Unknown')
            AS contributor,
            COUNT(*) AS total_commits,
            MAX(committed_at) AS last_commit
        FROM github_commits
        GROUP BY COALESCE(NULLIF(author_login, ''), author, 'Unknown')
        ORDER BY total_commits DESC
    """).fetchall()

    # TOTAL GITHUB COMMITS
    total_github_commits = conn.execute("""
        SELECT COUNT(*) FROM github_commits
    """).fetchone()[0]

    conn.close()

    return render_template(
        "index.html",
        members=members,
        contributions=contributions,
        member_counts=member_counts,
        total_members=total_members,
        total_contributions=total_contributions,
        date_labels=date_labels,
        daily_counts=daily_counts,
        github_commits=github_commits,
        total_github_commits=total_github_commits,
        github_member_counts=github_member_counts
    )


# ADD TEAM MEMBER
@app.route("/add-member", methods=["POST"])
def add_member():
    name = request.form.get("name", "").strip()

    if not name:
        flash("Please enter a member name.")
        return redirect(url_for("index"))

    conn = get_db()

    try:
        conn.execute(
            "INSERT INTO members (name) VALUES (?)",
            (name,)
        )
        conn.commit()
        flash("Team member added successfully!")

    except sqlite3.IntegrityError:
        flash("This team member already exists.")

    conn.close()

    return redirect(url_for("index"))


# ADD CONTRIBUTION
@app.route("/add-contribution", methods=["POST"])
def add_contribution():
    member_id = request.form.get("member_id")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "Other")

    allowed_categories = [
        "Feature", "Bug Fix", "Documentation",
        "Research", "Other"
    ]

    if not title or not member_id:
        flash("Please select a member and enter a title.")
        return redirect(url_for("index"))

    if category not in allowed_categories:
        category = "Other"

    conn = get_db()

    member = conn.execute(
        "SELECT id FROM members WHERE id = ?",
        (member_id,)
    ).fetchone()

    if member is None:
        flash("Selected team member does not exist.")

    else:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("""
            INSERT INTO contributions
            (member_id, title, description, category, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            member_id,
            title,
            description,
            category,
            created_at
        ))

        conn.commit()
        flash("Contribution recorded successfully!")

    conn.close()

    return redirect(url_for("index"))


# SYNC GITHUB COMMITS
@app.route("/sync-github", methods=["POST"])
def sync_github():

    token = os.getenv("GITHUB_TOKEN")

    if not token:
        flash("GitHub token not found. Check your .env file.")
        return redirect(url_for("index"))

    owner = request.form.get("owner", "").strip()
    repo = request.form.get("repo", "").strip()

    # Validate repository details
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", repo
    ):
        flash("Please enter valid GitHub owner and repository names.")
        return redirect(url_for("index"))

    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params={"per_page": 100},
            timeout=20
        )

        if response.status_code == 401:
            flash("GitHub token is invalid or expired. Check your token.")
            return redirect(url_for("index"))

        if response.status_code == 403:
            flash("GitHub access denied. Check your token permissions.")
            return redirect(url_for("index"))

        if response.status_code == 404:
            flash("Repository not found. Check the owner and repository name.")
            return redirect(url_for("index"))

        response.raise_for_status()

        commits = response.json()

        if not isinstance(commits, list):
            flash("GitHub returned an unexpected response.")
            return redirect(url_for("index"))

        conn = get_db()
        new_commits = 0

        for item in commits:
            commit_data = item.get("commit", {})
            author_data = commit_data.get("author") or {}
            github_author = item.get("author") or {}

            author_name = author_data.get("name") or "Unknown"
            author_login = github_author.get("login") or ""
            message = commit_data.get("message") or "No commit message"
            committed_at = author_data.get("date") or ""
            sha = item.get("sha")
            html_url = item.get("html_url") or ""

            if not sha:
                continue

            cursor = conn.execute("""
                INSERT OR IGNORE INTO github_commits
                (sha, author, author_login, message, committed_at, html_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                sha,
                author_name,
                author_login,
                message,
                committed_at,
                html_url
            ))

            new_commits += cursor.rowcount

        conn.commit()
        conn.close()

        flash(
            f"GitHub sync successful! "
            f"{new_commits} new commits added. "
            f"Repository: {owner}/{repo}"
        )

    except requests.exceptions.Timeout:
        flash("GitHub took too long to respond. Please try again.")

    except requests.exceptions.RequestException:
        flash("Could not connect to GitHub. Check your internet connection.")

    except ValueError:
        flash("Could not read GitHub's response.")

    return redirect(url_for("index"))


# START APPLICATION
if __name__ == "__main__":
    init_db()
    app.run(debug=True)

# SocialMediaPlugin Overview

This document explains the current frontend and backend features, core use cases, and how the app is organized.

## Product Summary

SocialMediaPlugin is a simple, single-uploader audio submission tool where one user uploads a track and provides:
- Tempo (BPM)
- Genre
- An AI-generated description

There is no branching, collaboration, waveform display, or grid editor.

## Frontend Features

### Upload Page
- Single upload form for one audio file.
- Fields: tempo (BPM), genre, AI description.
- Basic playback of the uploaded audio file (no waveform).
- Page:
  - `branchjam/templates/upload.html`

### Submissions List
- Shows submitted tracks with their metadata.
- Page:
  - `branchjam/templates/submissions.html`

## Backend Features

### App Setup
- Flask app factory in `branchjam/__init__.py`
- SQLite DB at `branchjam.db`
- Uploads stored in `uploads/`
- WAV-only validation

### Upload Blueprint (`branchjam/uploads.py`)
- Create a new submission.
- Store metadata (tempo, genre, AI description).
- List submitted tracks.

## Primary Use Cases

1. **Submit Audio**
   - Upload a WAV.
   - Provide tempo, genre, and AI description.

2. **Review Submissions**
   - Browse the list of uploads and metadata.
   - Play back the audio.

## Data Model (High Level)

- `users`: account identity (optional if single-user mode).
- `submissions`: audio file + tempo + genre + AI description + timestamps.

## Current Constraints & Assumptions

- WAV-only audio input.
- Single-uploader workflow.
- Development server by default in `SocialMedia.py`.

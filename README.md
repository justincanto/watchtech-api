# YouTube Video Analysis API

This project provides an API for processing YouTube videos to generate transcripts, summaries, and mindmaps.

## Features

- Extract transcripts from YouTube videos
- Generate concise summaries of video content with timecodes
- Create mermaid.js mindmaps visualizing key topics and relationships

## Installation

1. Clone the repository
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Make sure you have a valid Mistral API key set in your .env file or as an environment variable:

```
MISTRAL_API_KEY=your_api_key_here
```

## Running the API Server

Start the server with:

```bash
python api.py
```

The server will run at `http://localhost:8000` by default.

## API Endpoints

### Process Video

```
POST /process-video
```

Process a YouTube video to generate a transcript, summary, and mindmap.

**Request Body:**

```json
{
  "url": "https://www.youtube.com/watch?v=video_id"
}
```

**Response:**

```json
{
  "summary": "Detailed summary of the video content with timecodes",
  "mindmap": "Mermaid.js mindmap representation of the content"
}
```

### Health Check

```
GET /health
```

Simple health check endpoint.

**Response:**

```json
{
  "status": "healthy"
}
```

## API Documentation

The API documentation is available at `http://localhost:8000/docs` when the server is running.

## Error Handling

The API returns appropriate HTTP status codes and error messages for different error scenarios:

- `400`: Bad Request - Invalid input
- `500`: Internal Server Error - Server-side processing error

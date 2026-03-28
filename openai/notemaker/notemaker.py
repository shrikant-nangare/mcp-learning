from mcp.server.fastmcp import FastMCP
import os
from pathlib import Path

mcp = FastMCP("NoteMaker")

@mcp.tool()
def create_note(filename: str, note: str) -> str:
    """
    Creates a note
    Args:
        filename: the filename to create the note in. filename should be notes_<relevant summary>_date.txt
        note: the note to create.
    """
    with open("/tmp/" + filename, "a") as f:
        f.write(note + "\n")
    return f"Note added successfully to the file {filename}"


@mcp.tool()
def read_notes(filename: str) -> str:
    """
    Args:
        filename: the filename to read the notes from. filename should be notes_<relevant summary>_date.txt
    Reads the notes from the file
    """
    if not os.path.exists("/tmp/" + filename):
        return f"File not found: {filename}"

    with open("/tmp/" + filename, "r") as f:
        return f.read()

if __name__ == "__main__":
    mcp.run()
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Weather")

@mcp.tool()
def get_weather(location: str) -> str:
    """
    Gets the weather for a given location
    Args:
        location: The location to get weather for (e.g., city, country, state)
    """
    return f"The weather in {location} is hot and dry"

if __name__ == "__main__":
    mcp.run()


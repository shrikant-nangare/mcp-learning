from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import asyncio
import traceback

server_params = StdioServerParameters(
    command="uv",
    args=["--directory", "/Users/shrikant/repos/mcp-learning/helloworld", "run", "weather.py"],
)

async def run():
    try:
        print("Starting stdio_client...")
        async with stdio_client(server_params) as (read, write):
            print("Client connected, creating session...")
            async with ClientSession(read, write) as session:

                print("Initializing session...")
                await session.initialize()

                # List available tools
                print("\nListing tools...")
                tools = await session.list_tools()
                print(f"Available tools: {tools}")

                # Call the get_weather tool
                print("\nCalling get_weather tool...")
                result = await session.call_tool("get_weather", arguments={"location": "San Francisco"})
                print(f"Weather result: {result}")

                # Try another location
                print("\nCalling get_weather tool for another location...")
                result = await session.call_tool("get_weather", arguments={"location": "New York"})
                print(f"Weather result: {result}")

    except Exception as e:
        print("An error occurred:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run())


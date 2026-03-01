from openai import OpenAI

client = OpenAI()

stream = client.responses.create(
    model="gpt-5.2",
    input=[
        {
            "role": "user",
            "content": "Say 'double bubble bath' every 5 seconds for 10 times.",
        },
    ],
    stream=True,
)

for event in stream:
    chunk = getattr(event, "delta", None) or getattr(event, "text", None)
    if isinstance(chunk, str) and chunk:
        print(chunk, end="", flush=True)
print()
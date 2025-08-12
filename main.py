import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

class GooClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message: discord.Message):
        print(f'Message from {message.author}: {message.content}')

        if message.author == self.user:
            return
        
        if message.channel.id == 1379490638281834626:
            if message.content.startswith('!hopinmygoo'):
                response_string = f'no please {message.author} i dont want to hop in your goo'
                await message.channel.send(response_string)

def main():
    print("Hello from goobot!")
    intents = discord.Intents.default()
    intents.message_content = True

    client = GooClient(intents=intents)
    client.run(TOKEN)

if __name__ == "__main__":
    main()

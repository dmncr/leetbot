# LeetBot - IRC Bot for 1337 Time

A fun IRC bot that manages a game around typing "1337" at 13:37. Players compete to get the highest score by being the most precise with their timing.

## Features

- Score tracking for daily, weekly, monthly, and yearly periods
- Multiple command support (`!help`, `!time`, `!highscores`, etc.)
- Persistent score storage
- Configurable IRC server and channel settings
- Docker containerized for easy deployment

## Quick Start

1. Clone the repository
```bash
git clone https://github.com/yourusername/leetbot.git
cd leetbot
```

2. Create your configuration
```bash
cp docker-compose.yaml.default docker-compose.yaml
```

3. Edit the docker-compose.yaml file with your IRC settings:
- IRC_SERVER: Your IRC server address
- IRC_PORT: IRC server port (usually 6667)
- IRC_CHANNEL: The channel to join
- IRC_NICKNAME: Bot's nickname

4. Build and start the container
```bash
docker-compose up -d
```

## Commands

- `!help` - Show help message
- `!time` - Show current server time
- `!timetest` - Test server time
- `!highscores` - Display current high scores
- `!toptoday` - Show top 5 players today
- `!topweek` - Show top 5 players this week
- `!topmonth` - Show top 5 players this month
- `!topyear` - Show top 5 players this year
- `!statistics` - Show lifetime statistics

## Game Rules

1. The game happens every day at 13:37
2. Type "1337" (or variations like "leet") between 13:37:00 and 13:38:00
3. Score is based on how close you are to 13:37:37.037
4. The closer to the perfect time, the higher your score
5. Scores are tracked daily, weekly, monthly, and yearly

## Configuration

The bot is configured through environment variables in the docker-compose.yaml file:

```yaml
environment:
  - IRC_SERVER=irc.server.address
  - IRC_PORT=6667
  - IRC_CHANNEL=#yourchannel
  - IRC_NICKNAME=YourBotName
```

## Data Storage

Scores are stored in a JSON file that persists between container restarts. The location can be configured in the docker-compose.yaml file.

## Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Create a new Pull Request

## License

This project is open source and available under the MIT License.

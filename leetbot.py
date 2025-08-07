import irc.bot
import irc.strings
from irc.client import ip_numstr_to_quad, ip_quad_to_numstr
import time
import datetime
import threading
import json
import os
import re

# Get configuration from environment variables with defaults
IRC_SERVER = os.getenv('IRC_SERVER', 'portlane.se.quakenet.org')
IRC_PORT = int(os.getenv('IRC_PORT', '6667'))
IRC_CHANNEL = os.getenv('IRC_CHANNEL', '#mbhooden')
IRC_NICKNAME = os.getenv('IRC_NICKNAME', 'LeetBot13373712')


class LeetBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channel, nickname, server, port=6667):
        super().__init__([(server, port)], nickname, nickname)
        self.channel = channel
        self.scores = {}
        self.lock = threading.Lock()
        self.load_scores()
        threading.Thread(target=self.schedule_announcements, daemon=True).start()

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")

    def on_welcome(self, c, e):
        c.join(self.channel)
        c.privmsg(self.channel, "LeetBot is now online! Type '!help' for commands.")

    def on_pubmsg(self, c, e):
        now = datetime.datetime.now()
        start_time = now.replace(hour=13, minute=37, second=0, microsecond=0)
        end_time = now.replace(hour=13, minute=38, second=0, microsecond=0)
        message = e.arguments[0].strip()
        nick = e.source.nick

        if nick != self.connection.get_nickname() and '!timetest' in message.lower():
            self.send_time(e, now)
        elif start_time <= now <= end_time and self.is_leet_message(message):
            score = self.calculate_score(now)
            with self.lock:
                self.update_scores(nick, score, now)
        elif message.lower() == '!help':
            self.send_help(e)
        elif message.lower() == '!time':
            self.send_time(e, now)
        elif message.lower() == '!highscores':
            self.send_highscores(e)
        elif message.lower() in ['!toptoday', '!topweek', '!topmonth', '!topyear']:
            self.send_top_scores(e, message.lower()[4:])  # Remove '!top' prefix
        elif message.lower() == '!statistics':
            self.send_statistics(e)

    def is_leet_message(self, message):
        pattern = re.compile(r'\b(1|i|l)(3|e){2}(7|t)\b', re.IGNORECASE)
        return bool(pattern.search(message))

    def calculate_score(self, timestamp):
        target_times = [
            timestamp.replace(hour=13, minute=37, second=37, microsecond=37),
        ]
        min_diff = min(abs((timestamp - t).total_seconds()) for t in target_times)
        
        if min_diff > 13:
            return 1
            
        # Calculate logarithmic score between 0-13 seconds
        # Using natural log to create smooth falloff
        # Subtract from 100 so closer times = higher scores
        score = 100 - (100 * (min_diff / 13))
        return score

    def update_scores(self, nick, score, timestamp):
        date = timestamp.date()
        week = date.isocalendar()[1]
        month = date.month
        year = date.year
        day = date.day

        self.scores.setdefault('daily', {})
        self.scores.setdefault('weekly', {})
        self.scores.setdefault('monthly', {})
        self.scores.setdefault('yearly', {})

        daily_key = f'{year}-{month}-{day}'
        weekly_key = f'{year}-W{week}'
        monthly_key = f'{year}-{month}'
        yearly_key = f'{year}'

        self.scores['daily'].setdefault(daily_key, {})
        self.scores['weekly'].setdefault(weekly_key, {})
        self.scores['monthly'].setdefault(monthly_key, {})
        self.scores['yearly'].setdefault(yearly_key, {})

        # Store contestant details including timestamp and score
        self.scores['daily'].setdefault(daily_key + '_contestants', [])
        self.scores['daily'][daily_key + '_contestants'].append({
            'nick': nick,
            'timestamp': timestamp.strftime('%H:%M:%S.%f'),
            'score': score
        })

        # Helper function to update score with timestamp
        def update_period_score(period_dict, key, nick, new_score, timestamp_str):
            current = period_dict.get(key, {}).get(nick, {'score': 0})
            if isinstance(current, (int, float)):  # Handle old format
                current = {'score': current}
            if new_score > current['score']:
                period_dict[key][nick] = {
                    'score': new_score,
                    'timestamp': timestamp_str
                }

        timestamp_str = timestamp.strftime('%H:%M:%S.%f')
        update_period_score(self.scores['daily'], daily_key, nick, score, timestamp_str)
        update_period_score(self.scores['weekly'], weekly_key, nick, score, timestamp_str)
        update_period_score(self.scores['monthly'], monthly_key, nick, score, timestamp_str)
        update_period_score(self.scores['yearly'], yearly_key, nick, score, timestamp_str)

        self.save_scores()

    def save_scores(self):
        with open('scores.json', 'w') as f:
            json.dump(self.scores, f)

    def load_scores(self):
        try:
            if os.path.exists('scores.json') and os.path.getsize('scores.json') > 0:
                with open('scores.json', 'r') as f:
                    self.scores = json.load(f)
            else:
                self.scores = {
                    'daily': {},
                    'weekly': {},
                    'monthly': {},
                    'yearly': {}
                }
        except json.JSONDecodeError:
            # If the file is corrupted, initialize with empty structure
            self.scores = {
                'daily': {},
                'weekly': {},
                'monthly': {},
                'yearly': {}
            }

    def send_help(self, e):
        c = self.connection
        help_messages = [
            "Commands:",
            "!help - Show this help message.",
            "!timetest - Show current server time.",
            "!highscores - Display current high scores.",
            "!toptoday - Show top 5 players today (score and participation).",
            "!topweek - Show top 5 players this week.",
            "!topmonth - Show top 5 players this month.",
            "!topyear - Show top 5 players this year.",
            "!statistics - Show lifetime statistics for all players.",
            "Type '1337' or similar between 13:37:00 and 13:38:00 to participate."
        ]
        for line in help_messages:
            c.privmsg(e.target, line)

    def send_time(self, e, timestamp):
        c = self.connection
        time_str = timestamp.strftime('%H:%M:%S.%f')[:-3]  # Format with milliseconds
        c.privmsg(e.target, f"Current server time: {time_str}")

    def send_highscores(self, e):
        c = self.connection
        now = datetime.datetime.now()
        scores = self.get_current_scores(now)

        def format_scores(title, scores_dict):
            if scores_dict:
                # Extract score value, handling both old (number) and new (dict) formats
                def get_score(item):
                    nick, score_data = item
                    return score_data['score'] if isinstance(score_data, dict) else score_data
                
                sorted_scores = sorted(scores_dict.items(), key=lambda x: get_score(x), reverse=True)
                total_score = sum(get_score(item) for item in sorted_scores)
                avg_score = total_score / len(sorted_scores)
                
                scores_text = []
                for nick, score_data in sorted_scores:
                    if isinstance(score_data, dict):
                        score = score_data['score']
                        timestamp = score_data.get('timestamp', 'unknown time')
                        scores_text.append(f"{nick}: {score:.2f} ({timestamp})")
                    else:
                        scores_text.append(f"{nick}: {score_data:.2f}")
                        
                stats = f"(Total: {total_score:.2f}, Avg: {avg_score:.2f}, Participants: {len(sorted_scores)})"
                return f"{title}: {', '.join(scores_text)} {stats}"
            else:
                return f"{title}: No participants."

        messages = [
            format_scores("Daily High Scores", scores['daily']),
            format_scores("Weekly High Scores", scores['weekly']),
            format_scores("Monthly High Scores", scores['monthly']),
            format_scores("Yearly High Scores", scores['yearly']),
        ]
        for message in messages:
            c.privmsg(e.target, message)

    def get_current_scores(self, now):
        date = now.date()
        week = date.isocalendar()[1]
        month = date.month
        year = date.year
        day = date.day

        daily_key = f'{year}-{month}-{day}'
        weekly_key = f'{year}-W{week}'
        monthly_key = f'{year}-{month}'
        yearly_key = f'{year}'

        with self.lock:
            daily_scores = self.scores.get('daily', {}).get(daily_key, {})
            weekly_scores = self.scores.get('weekly', {}).get(weekly_key, {})
            monthly_scores = self.scores.get('monthly', {}).get(monthly_key, {})
            yearly_scores = self.scores.get('yearly', {}).get(yearly_key, {})

        return {
            'daily': daily_scores,
            'weekly': weekly_scores,
            'monthly': monthly_scores,
            'yearly': yearly_scores,
        }

    def schedule_announcements(self):
        while True:
            now = datetime.datetime.now()
            
            # Schedule pre-game announcement at 13:36:00
            pregame_time = now.replace(hour=13, minute=36, second=0, microsecond=0)
            if now >= pregame_time:
                pregame_time += datetime.timedelta(days=1)
            
            # Schedule post-game announcement at 13:38:30
            target_time = now.replace(hour=13, minute=38, second=30, microsecond=0)
            if now >= target_time:
                target_time += datetime.timedelta(days=1)
            
            # Sleep until next scheduled announcement
            next_time = min(pregame_time, target_time)
            time.sleep((next_time - now).total_seconds())
            
            # Send appropriate announcement
            if next_time == pregame_time:
                time_str = pregame_time.strftime('%H:%M:%S.%f')[:-3]  # Format with milliseconds like !timetest
                self.connection.privmsg(self.channel, f"The game of games is about to begin! Server time is: {time_str}")
            else:
                self.make_announcements()

    def make_announcements(self):
        c = self.connection
        now = datetime.datetime.now()
        scores = self.get_current_scores(now)

        def format_period_stats(scores_dict, period):
            if scores_dict:
                def get_score(item):
                    nick, score_data = item
                    return score_data['score'] if isinstance(score_data, dict) else score_data
                
                total_score = sum(get_score(item) for item in scores_dict.items())
                avg_score = total_score / len(scores_dict)
                sorted_scores = sorted(scores_dict.items(), key=lambda x: get_score(x), reverse=True)
                high_scorer = sorted_scores[0]
                
                winner_score = get_score(('', high_scorer[1]))
                winner_time = high_scorer[1].get('timestamp', '') if isinstance(high_scorer[1], dict) else ''
                winner_time_str = f" at {winner_time}" if winner_time else ""
                
                summary = (f"{period}'s winner is {high_scorer[0]} with a score of {winner_score:.2f}{winner_time_str}! " 
                         f"Total score: {total_score:.2f}, Average: {avg_score:.2f}, "
                         f"Participants: {len(scores_dict)}")
                
                # Format all player scores with timestamps
                scores_list = []
                for nick, score_data in sorted_scores:
                    score = get_score(('', score_data))
                    timestamp = score_data.get('timestamp', '') if isinstance(score_data, dict) else ''
                    time_str = f" at {timestamp}" if timestamp else ""
                    scores_list.append(f"{nick}: {score:.2f}{time_str}")
                
                return [summary, f"All scores: {', '.join(scores_list)}"]
            return [f"No participants {period.lower()}."]

        # Daily announcements
        if scores['daily']:
            messages = format_period_stats(scores['daily'], "Today")
            for message in messages:
                c.privmsg(self.channel, message)

            # Get today's contestants list with attempt details
            date = now.date()
            daily_key = f'{date.year}-{date.month}-{date.day}'
            contestants = self.scores['daily'].get(daily_key + '_contestants', [])
            if contestants:
                c.privmsg(self.channel, "Today's attempts in chronological order:")
                for contestant in sorted(contestants, key=lambda x: x['timestamp']):
                    c.privmsg(self.channel, 
                        f"{contestant['nick']} at {contestant['timestamp']} - Score: {contestant['score']:.2f}")
        else:
            c.privmsg(self.channel, "No participants today.")

        # Weekly announcements (on Sunday)
        if now.weekday() == 6:
            messages = format_period_stats(scores['weekly'], "This week")
            for message in messages:
                c.privmsg(self.channel, message)

        # Monthly announcements (on first day of month)
        if now.day == 1:
            last_month = (now.replace(day=1) - datetime.timedelta(days=1)).month
            last_month_key = f'{now.year}-{last_month}'
            monthly_scores = self.scores.get('monthly', {}).get(last_month_key, {})
            messages = format_period_stats(monthly_scores, "Last month")
            for message in messages:
                c.privmsg(self.channel, message)

        # Yearly announcements (on first day of year)
        if now.month == 1 and now.day == 1:
            last_year = now.year - 1
            yearly_scores = self.scores.get('yearly', {}).get(str(last_year), {})
            messages = format_period_stats(yearly_scores, "Last year")
            for message in messages:
                c.privmsg(self.channel, message)

    def on_disconnect(self, c, e):
        self.connect()

    def send_statistics(self, e):
        c = self.connection
        user_stats = {}

        # Collect all daily contestant entries for complete statistics
        for daily_key, daily_data in self.scores['daily'].items():
            if not daily_key.endswith('_contestants'):
                continue

            for entry in daily_data:
                nick = entry['nick']
                score = entry['score']
                
                if nick not in user_stats:
                    user_stats[nick] = {
                        'tries': 0,
                        'total_score': 0,
                        'max_score': 0,
                        'scores': []
                    }
                
                stats = user_stats[nick]
                stats['tries'] += 1
                stats['total_score'] += score
                stats['max_score'] = max(stats['max_score'], score)
                stats['scores'].append(score)

        # Sort users by number of tries, then by max score
        sorted_users = sorted(
            user_stats.items(),
            key=lambda x: (-x[1]['tries'], -x[1]['max_score'])
        )

        c.privmsg(e.target, "Lifetime Statistics:")
        for nick, stats in sorted_users:
            avg_score = stats['total_score'] / stats['tries']
            c.privmsg(e.target, 
                f"{nick}: {stats['tries']} tries, "
                f"Max score: {stats['max_score']:.2f}, "
                f"Average score: {avg_score:.2f}")

    def send_top_scores(self, e, period):
        c = self.connection
        now = datetime.datetime.now()
        date = now.date()
        
        # Determine the key based on period
        if period == 'today':
            key = f'{date.year}-{date.month}-{date.day}'
            scores_dict = self.scores.get('daily', {}).get(key, {})
            title = "Today's"
            contestants_key = key + '_contestants'
            contestants = self.scores.get('daily', {}).get(contestants_key, [])
        elif period == 'week':
            week = date.isocalendar()[1]
            key = f'{date.year}-W{week}'
            scores_dict = self.scores.get('weekly', {}).get(key, {})
            title = "This week's"
        elif period == 'month':
            key = f'{date.year}-{date.month}'
            scores_dict = self.scores.get('monthly', {}).get(key, {})
            title = "This month's"
        else:  # year
            key = f'{date.year}'
            scores_dict = self.scores.get('yearly', {}).get(key, {})
            title = "This year's"

        if not scores_dict:
            c.privmsg(e.target, f"No participants {period}.")
            return

        # Get participation counts
        participation_counts = {}
        if period == 'today':
            # For today, we can use the contestants list directly
            for entry in contestants:
                participation_counts[entry['nick']] = participation_counts.get(entry['nick'], 0) + 1
        else:
            # For other periods, we need to count across all relevant daily entries
            for daily_key, daily_data in self.scores['daily'].items():
                if not daily_key.endswith('_contestants'):
                    continue
                date_str = daily_key[:-12]  # Remove '_contestants'
                year, month, day = map(int, date_str.split('-'))
                entry_date = datetime.date(year, month, day)
                
                # Check if the entry belongs to the current period
                if period == 'week' and entry_date.isocalendar()[1] == date.isocalendar()[1]:
                    contestants = daily_data
                elif period == 'month' and entry_date.month == date.month:
                    contestants = daily_data
                elif period == 'year' and entry_date.year == date.year:
                    contestants = daily_data
                else:
                    continue
                    
                for entry in contestants:
                    participation_counts[entry['nick']] = participation_counts.get(entry['nick'], 0) + 1

        # Sort by score and get top 5
        def get_score(item):
            nick, score_data = item
            return score_data['score'] if isinstance(score_data, dict) else score_data

        top_scores = sorted(scores_dict.items(), key=lambda x: get_score(x), reverse=True)[:5]
        
        # Format and send the message
        c.privmsg(e.target, f"{title} Top 5:")
        for i, (nick, score_data) in enumerate(top_scores, 1):
            score = get_score(('', score_data))
            participations = participation_counts.get(nick, 0)
            timestamp = score_data.get('timestamp', '') if isinstance(score_data, dict) else ''
            time_str = f" at {timestamp}" if timestamp else ""
            c.privmsg(e.target, 
                f"{i}. {nick} - Score: {score:.2f}{time_str}, Participations: {participations}")

def main():
    # Use environment variables configured at the top of the file
    bot = LeetBot(IRC_CHANNEL, IRC_NICKNAME, IRC_SERVER, IRC_PORT)
    bot.start()

if __name__ == "__main__":
    main()


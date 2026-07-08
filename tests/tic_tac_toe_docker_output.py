```python
import random
import time

def print_board(board):
    print(f" {board[0]} | {board[1]} | {board[2]}")
    print("---+---+---")
    print(f" {board[3]} | {board[4]} | {board[5]}")
    print("---+---+---")
    print(f" {board[6]} | {board[7]} | {board[8]}")

def check_win(board):
    win_conditions = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
    for condition in win_conditions:
        if board[condition[0]] == board[condition[1]] == board[condition[2]] != " ":
            return board[condition[0]]
    if " " not in board:
        return "Tie"
    return False

def play_game():
    board = [" "] * 9
    players = ["X", "O"]
    current_player = 0
    while True:
        print_board(board)
        available_moves = [i for i, x in enumerate(board) if x == " "]
        move = random.choice(available_moves)
        board[move] = players[current_player]
        result = check_win(board)
        if result:
            print_board(board)
            if result == "Tie":
                print("It's a tie!")
            else:
                print(f"Player {result} wins!")
            break
        current_player = (current_player + 1) % 2
        time.sleep(1)

if __name__ == "__main__":
    play_game()
```
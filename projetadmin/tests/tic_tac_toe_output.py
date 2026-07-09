```python
import random

def print_board(board):
    print(f" {board[0]} | {board[1]} | {board[2]} ")
    print("---+---+---")
    print(f" {board[3]} | {board[4]} | {board[5]} ")
    print("---+---+---")
    print(f" {board[6]} | {board[7]} | {board[8]} ")

def check_win(board):
    winning_combos = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
    for combo in winning_combos:
        if board[combo[0]] == board[combo[1]] == board[combo[2]] != " ":
            return board[combo[0]]
    if " " not in board:
        return "Tie"
    return False

def main():
    board = [" "] * 9
    players = ["X", "O"]
    current_player = 0
    while True:
        available_moves = [i for i, x in enumerate(board) if x == " "]
        move = random.choice(available_moves)
        board[move] = players[current_player]
        print_board(board)
        result = check_win(board)
        if result:
            if result == "Tie":
                print("It's a tie!")
            else:
                print(f"Player {result} wins!")
            break
        current_player = (current_player + 1) % 2

if __name__ == "__main__":
    main()
```
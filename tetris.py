from aiohttp import web, WSMsgType
import asyncio
import random
import json
from constants import TETS, BOARD_WIDTH, BOARD_HEIGHT

routes = web.RouteTableDef()

# Global state
playws    = {}   
watchws   = {}   
watch_map = {}   
games     = {}   

async def send_to(ws, msg):
    try:
        await ws.send_json(msg)
        game_id = None
        for gid, gws in playws.items():
            if gws is ws:
                game_id = gid
                break
        
        watchers = watch_map.get(game_id, set())
        for watcher_id in list(watchers):
            if not watcher_id.closed:
                await watcher_id.send_json(msg)
    except Exception as e:
        pass

class TetrisGame:
    def __init__(self, websocket):
        self.websocket = websocket  
        self.lock = asyncio.Lock()
        self.board = [[0 for x in range(BOARD_WIDTH)] for y in range(BOARD_HEIGHT)]  
        self.previous_board = None  
        shape, orientation, x, y = self.new_tetromino()
        max_down = self.max_moves_down(shape, orientation, x, y)
        self.live = {
            "shape": shape,
            "orientation": orientation,
            "x": x,
            "y": y,
            "max_moves_down": max_down
        }
        shape, orientation, x, y = self.new_tetromino()
        self.next = {
            "shape": shape,
            "orientation": orientation,
            "x": x,
            "y": y
        }
        self.fall_task = asyncio.create_task(self.fall())
        self.state_task = asyncio.create_task(self.send_state())
        self.running = True

    def new_tetromino(self):
        shape = random.randint(1, len(TETS))
        orientation = 0  
        x,y = self.get_initial_coordinate(shape, orientation)
        return shape, orientation, x, y

    def create_live_tetromino(self):
        self.live = self.next
        self.live["max_moves_down"] = self.max_moves_down(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        if self.collides(self.get_absolute_coordinates(self.live["shape"], self.live["orientation"],self.live["x"], self.live["y"])):
            self.game_over()
            return
        shape, orientation, x, y = self.new_tetromino()
        self.next = {
            "shape": shape,
            "orientation": orientation,
            "x": x,
            "y": y
        }

    def get_initial_coordinate(self, shape, orientation):
        relative_coords = TETS[shape - 1][orientation] 
        point = (4, 0)  
        absolute_coords = [(x + point[0], y + point[1]) for (x, y) in relative_coords] 
        x_coord, y_coord = zip(*absolute_coords)
        min_y = abs(min(y_coord))
        reference_pt = (4, min_y)
        return reference_pt

    def get_absolute_coordinates(self, shape, orientation, x, y):
        relative_coords = TETS[shape - 1][orientation]
        absolute_coords = [(dx + x, dy + y) for dx, dy in relative_coords]
        return absolute_coords
    
    def max_moves_down(self, shape, orientation, x, y):
        relative_blocks = TETS[shape - 1][orientation]
        distance = 0
        for distance in range(1, BOARD_HEIGHT):
            test_blocks = [(dx + x, dy + distance + y) for (dx, dy) in relative_blocks]
            if self.collides(test_blocks):
                return distance - 1  
        return BOARD_HEIGHT - 1  

    def collides(self, blocks):
        for (x, y) in blocks:
            if x < 0 or x >= BOARD_WIDTH or y < 0 or y >= BOARD_HEIGHT:
                return True
            if self.board[y][x] != 0:
                return True
        return False
    
    async def send_state(self):
        live_data = [
            self.live["shape"],
            self.live["orientation"],
            self.live["x"],
            self.live["y"],
            self.live["max_moves_down"]
        ]
        current_board = self.encode_board()
        await send_to(self.websocket, {"live": live_data, "board": current_board, "next": self.next["shape"]})
        self.previous_board = current_board 

    def encode_board(self):
        encoded = []
        for row in self.board:
            val = 0
            for cell in row:
                val = (val << 3) | cell
            encoded.append(val)
        return encoded

    async def fall(self):
        while self.running:
            await asyncio.sleep(0.5)
            await self.move_down()

    async def handle_messages(self, msg):
        commands = {
            "cw":    lambda: self.rotate("cw"),
            "ccw":   lambda: self.rotate("ccw"),
            "left":  self.move_left,
            "right": self.move_right,
            "down":  self.move_down,
            "drop":  self.drop
        }
        
        if msg in commands:
            await commands[msg]()

    async def move_down(self):
        if self.live['max_moves_down'] > 0:
            self.live['y'] += 1
            self.live['max_moves_down'] -= 1
            await self.send_state()
        else:
            self.place_on_board()
            self.board = self.clear_rows()
            self.create_live_tetromino()
            await self.send_state()

    async def move_left(self):
        self.live['x'] -= 1
        new_coords = self.get_absolute_coordinates(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        if self.collides(new_coords):
            self.live['x'] += 1
            return
        self.live['max_moves_down'] = self.max_moves_down(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        await self.send_state()

    async def move_right(self):
        self.live['x'] += 1  
        new_coords = self.get_absolute_coordinates(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        if self.collides(new_coords):
            self.live['x'] -= 1  
            return
        self.live['max_moves_down'] = self.max_moves_down(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        await self.send_state()

    async def rotate(self, direction):
        if direction == "cw":
            new_orientation = (self.live["orientation"] + 1) % len(TETS[self.live["shape"] - 1])
        else:
            new_orientation = (self.live["orientation"] - 1) % len(TETS[self.live["shape"] - 1])

        new_coords = self.get_absolute_coordinates(self.live["shape"], new_orientation, self.live["x"], self.live["y"])

        # out-of-bounds negative Y
        min_y = min(y for m, y in new_coords)
        adjusted_y = self.live["y"]
        if min_y < 0:
            shift_y = -min_y
            adjusted_y += shift_y
            new_coords = self.get_absolute_coordinates(self.live["shape"], new_orientation, self.live["x"], adjusted_y)

        if self.collides(new_coords):
            wall_kick_directions = [(-1, 0), (1, 0), (-2, 0), (2, 0)]
            for (dx, dy) in wall_kick_directions:
                moved_x = self.live["x"] + dx
                moved_y = adjusted_y + dy
                test_coords = self.get_absolute_coordinates(self.live["shape"], new_orientation, moved_x, moved_y)
                if not self.collides(test_coords):
                    self.live["x"] = moved_x
                    self.live["y"] = moved_y
                    self.live["orientation"] = new_orientation
                    break
        else:
            self.live["orientation"] = new_orientation
            self.live["y"] = adjusted_y

        self.live["max_moves_down"] = self.max_moves_down(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        await self.send_state()

    async def drop(self):
        self.live['y'] += self.live['max_moves_down']
        self.live['max_moves_down'] = 0
        self.place_on_board()
        self.board = self.clear_rows()
        self.create_live_tetromino()
        if not self.running:
            await send_to(self.websocket, {
                "live":  [self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"], self.live["max_moves_down"]],
                "next":  self.next["shape"],
                "board": self.encode_board(),
                "event": "gameover"
            })
        else:
            await self.send_state()

    def place_on_board(self):
        coords = self.get_absolute_coordinates(self.live["shape"], self.live["orientation"], self.live["x"], self.live["y"])
        for (x, y) in coords:
            self.board[y][x] = self.live["shape"]
        self.board = self.clear_rows()

    def game_over(self):
        self.running = False
        if self.fall_task: self.fall_task.cancel()
        if self.state_task: self.state_task.cancel()
        asyncio.create_task(send_to(self.websocket, {"event": "gameover"}))

    def clear_rows(self):
        new_board = [row for row in self.board if any(cell == 0 for cell in row)]
        # CHANGE 1: Used Constants
        while len(new_board) < BOARD_HEIGHT:
            new_board.insert(0, [0] * BOARD_WIDTH)
        return new_board

@routes.get('/ws')
async def websocket_handler(request : web.Request) -> web.WebSocketResponse:
    """The main event loop: accepts connections, manages games, cleans up"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    playws[id(ws)] = ws
    
    game = TetrisGame(ws)
    games[id(ws)] = game  

    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            await game.handle_messages(msg.data)
        elif msg.type == WSMsgType.ERROR:
            print(f'WebSocket received exception {ws.exception()}')

    del playws[id(ws)]
    del games[id(ws)]  
    return ws

@routes.get('/')
async def index(req : web.Request) -> web.FileResponse:
    return web.FileResponse(path="index.html")

@routes.get('/watch')
async def watch_page(req : web.Request) -> web.FileResponse:
    return web.FileResponse(path="watch.html")

@routes.get('/snoop')
async def snoop(request : web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            txt = msg.data.strip()
            if txt == '?':
                await ws.send_json({"alive": list(playws.keys())})
            else:
                try:
                    game_id = int(txt)
                    if game_id in playws:
                        for watchers in watch_map.values():
                            watchers.discard(ws)
                        watch_map.setdefault(game_id, set()).add(ws)
                        await games[game_id].send_state()
                except ValueError: pass
        elif msg.type == WSMsgType.ERROR:
            break
    if not ws.closed: await ws.close()
    for watchers in watch_map.values():
        watchers.discard(ws)
    return ws

async def shutdown_ws(app: web.Application) -> None:
    for ws in list(playws.values()):
        await ws.close()
    for ws in list(watchws.values()):
        await ws.close()

def setup_app(app: web.Application) -> None:
    app.on_shutdown.append(shutdown_ws)
    app.add_routes(routes)
    
if __name__ == '__main__': 
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=str, default="0.0.0.0")
    parser.add_argument('-p','--port', type=int, default=8080) 
    args = parser.parse_args()

    app = web.Application()
    setup_app(app)
    web.run_app(app, host=args.host, port=args.port)
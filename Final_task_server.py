import airsim
from abc import ABC, abstractmethod
import concurrent.futures
import time
import websockets
import asyncio
import logging
import signal
import aiohttp_cors
from aiohttp import web
from websockets.exceptions import ConnectionClosedError

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

drones = [
    {"id": "drone1", "name": "Дрон 1"},
    {"id": "drone2", "name": "Дрон 2"},
    {"id": "drone3", "name": "Дрон 3"}
]

drones_locks = {}


# Паттерн "Команда" (Command Pattern)
# Класс для управления дроном
class IDrone(ABC):
    def __init__(self):
        self.orientation = 0  # текущая ориентация дрона в градусах

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def arm(self):
        pass

    @abstractmethod
    def takeoff(self):
        pass

    @abstractmethod
    def takeoff_cancel(self):
        pass

    @abstractmethod
    def land(self):
        pass

    @abstractmethod
    def land_cancel(self):
        pass

    @abstractmethod
    def rotate(self, degree: int):
        pass

    @abstractmethod
    def rotate_cancel(self, degree: int):
        pass

    @abstractmethod
    def change_altitude(self, altitude: int):
        pass

    @abstractmethod
    def change_altitude_cancel(self, altitude: int):
        pass

    @abstractmethod
    def drop_payload(self):
        pass

    @abstractmethod
    def drop_payload_cancel(self):
        pass


class DroneAirSim(IDrone):
    def connect(self):
        self.client = airsim.MultirotorClient()
        self.client.confirmConnection()
        logging.info("Подключились к дрону в AirSim")

    def arm(self):
        # Разблокировка управления и взлет
        self.client.enableApiControl(True)
        self.client.armDisarm(True)
        logging.info("Выполнено армирование")

    def takeoff(self):
        # Взлет до высоты 3 метра
        self.client.takeoffAsync().join()
        self.client.moveToZAsync(-3, 1).join()
        logging.info("Взлет до высоты 3 метра")

    def takeoff_cancel(self):
        self.takeoff()

    def land(self):
        self.client.landAsync().join()
        logging.info('Дрон: приземлился')

    def land_cancel(self):
        self.land()

    def rotate(self, degree: float, duration: float):
        # Метод для поворота дрона на заданное количество градусов
        logging.info(f'Дрон: поворот на {degree} градусов')
        # Изменяем ориентацию дрона и приводим значение к диапазону 0-359 градусов
        self.orientation = (self.orientation + degree) % 360
        self.client.rotateByYawRateAsync(degree, duration).join()

    def rotate_cancel(self, degree: float, duration: float):
        # Метод для отмены поворота дрона (поворот в обратную сторону)
        self.rotate(-degree, duration)

    def change_altitude(self, altitude: int):
        # Метод для изменения высоты полета дрона
        logging.info(f'Дрон: набор высоты до {altitude} м')
        self.client.moveToZAsync(altitude, 1).join()

    def change_altitude_cancel(self, altitude: int):
        self.change_altitude(-altitude)

    def drop_payload(self):
        # Метод для сброса нагрузки с дрона
        logging.info("Дрон: сброс груза")

    def drop_payload_cancel(self):
        self.drop_payload()


# Интерфейс команды (абстрактный класс)
class ICommand(ABC):
    def __init__(self, IDrone: IDrone):
        # Сохраняем ссылку на объект дрона
        self._IDrone = IDrone

    @abstractmethod
    def execute(self):
        # Абстрактный метод для выполнения команды
        pass

    @abstractmethod
    def undo(self):
        # Абстрактный метод для отмены команды
        pass

    def reset(self):
        # Сброс параметров команды перед возвратом в пул
        pass


# Команда для взлета
class TakeoffCommand(ICommand):
    def execute(self):
        # Выполнение команды взлета
        self._IDrone.takeoff()

    def undo(self):
        # Отмена взлета
        self._IDrone.takeoff_cancel()


# Команда для приземления
class LandCommand(ICommand):
    def execute(self):
        # Выполнение команды приземления
        self._IDrone.land()

    def undo(self):
        # Отмена приземления
        self._IDrone.land_cancel()


# Команда для сброса нагрузки
class DropPayloadCommand(ICommand):
    def execute(self):
        # Выполнение команды сброса нагрузки
        self._IDrone.drop_payload()

    def undo(self):
        # Отмена сброса нагрузки
        self._IDrone.drop_payload_cancel()


# Команда для изменения высоты полета
class ChangeAltitudeCommand(ICommand):
    def __init__(self, IDrone: IDrone, altitude: int):
        # Инициализация команды с заданной высотой
        super().__init__(IDrone)
        self._altitude = altitude

    def execute(self):
        # Выполнение команды изменения высоты
        self._IDrone.change_altitude(self._altitude)

    def undo(self):
        # Отмена изменения высоты
        self._IDrone.change_altitude_cancel(self._altitude)


# Команда для поворота дрона
class RotateCommand(ICommand):
    def __init__(self, IDrone: IDrone, degree: int):
        # Инициализация команды с заданным углом поворота
        super().__init__(IDrone)
        self._degree = degree

    def execute(self):
        # Выполнение команды поворота
        self._IDrone.rotate(self._degree)

    def undo(self):
        # Отмена поворота (поворот в обратную сторону)
        self._IDrone.rotate_cancel(self._degree)


# Класс Invoker, управляющий выполнением команд
class Invoker:
    def __init__(self):
        self._commands = []  # Очередь команд для выполнения
        self._executed_commands = []  # Список выполненных команд

    def add_command(self, command: ICommand):
        # Добавление команды в очередь
        self._commands.append(command)

    def execute(self):
        # Выполнение всех команд в очереди
        for command in self._commands:
            command.execute()
            self._executed_commands.append(command)
        # Очистка очереди после выполнения
        self._commands.clear()

    def undo(self):
        # Отмена последней выполненной команды
        if self._executed_commands:
            last_command = self._executed_commands.pop()
            last_command.undo()
        else:
            logging.info("Дрон отменил все действия")


class ObjectsPool:
    def __init__(self, obj, size):
        self._pool = [obj() for _ in range(size)]
        self._used = []

    def acquire(self):
        if len(self._pool) == 0:
            raise IndexError('Нет доступных объектов в пуле')
        obj = self._pool.pop()
        self._used.append(obj)
        return obj

    def release(self, obj):
        obj.reset()
        self._used.remove(obj)
        self._pool.append(obj)


class CommandPool:
    def __init__(self, command_type, *args, **kwargs):
        self.command = None
        if command_type == "rotate":
            self.command = RotateCommand(*args, **kwargs)
        elif command_type == "altitude":
            self.command = ChangeAltitudeCommand(*args, **kwargs)


def perform_command(pool, command_type, *args, **kwargs):
    command_pool = None
    try:
        command_pool = pool.acquire()
        command_pool = command_pool(command_type, *args, **kwargs)
        command_pool.command.execute()
    finally:
        if command_pool is not None:
            pool.release(command_pool)


async def get_drones(request):
    return web.json_response(drones)


async def control_drone(websocket):
    client_ip = websocket.remote_address[0]
    client_port = websocket.remote_address[1]
    logging.info(f"Подключен клиент: {client_ip}:{client_port}")

    command = {
        "takeoff": "Дрон взлетает",
        "land": "Дрон приземляется",
        "hover": "Дрон зависает",
        "move_forward": "Дрон летит вперед",
        "move_back": "Дрон летит назад"
    }

    selected_drone = None

    try:
        async for msg in websocket:
            if msg.startswith("selected_drone"):
                drone_id = msg.split()[1]
                if drone_id not in drones_locks:
                    drones_locks[drone_id] = (client_ip, client_port)
                    selected_drone = drone_id
                    await websocket.send(f"Выбран дрон {selected_drone}. Открыт доступ к управлению")
                else:
                    client_locked = drones_locks[drone_id]
                    if client_locked == (client_ip, client_port):
                        await websocket.send(f"Вы уже управляете дроном!")
                    else:
                        await websocket.send(f"Дрон {drone_id} уже занят другим оператором")
            elif selected_drone:
                # Теперь команды отправляются без указания дрона, так как он уже выбран
                logging.info(f"{client_ip}:{client_port} отправил команду для дрона {selected_drone}: {msg}")
                response = command.get(msg, "Неизвестная команда")
                await websocket.send(response)
            else:
                await websocket.send("Сначала выбери дрон!")

    except ConnectionClosedError as e:
        logging.warning(f"Соединение с клиентом {client_ip}:{client_port} закрыто: {e}")
    except Exception as e:
        logging.error(f"Необработанная ошибка для {client_ip}:{client_port}: {e}")
    finally:
        if selected_drone and drones_locks.get(selected_drone) == (client_ip, client_port):
            del drones_locks[selected_drone]
            logging.info(f"Освобожден дрон {selected_drone}")


async def shutdown_server(server, signal=None):
    if signal:
        logging.info(f"Получен сигнал завершения: {signal.name}")
        server.close()
        await server.wait_closed()
        logging.info(f"Сервер завершил работу")


async def main():
    start_server = await websockets.serve(control_drone, "localhost", 8765)
    logging.info(f"Сервер запущен и ожидает подключений")

    app = web.Application()
    app.router.add_get("/drones", get_drones)

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })

    for route in list(app.router.routes()):
        cors.add(route)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8081)
    await site.start()

    try:
        await start_server.wait_closed()
    except ConnectionClosedError as e:
        logging.warning(f"Соединение с клиентом закрыто: {e}")
    except Exception as e:
        logging.error(f"Необработанная ошибка: {e}")
    finally:
        start_server.close()
        await start_server.wait_closed()
        logging.error(f"Сервер завершил работу")

if __name__ == '__main__':
    drone = DroneAirSim()
    drone.connect()
    drone.arm()

    pool = ObjectsPool(CommandPool, 3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(perform_command, pool, "rotate", drone, 90, 1),
            executor.submit(perform_command, pool, "altitude", drone, 10)
        ]
        concurrent.futures.wait(futures)

    asyncio.run(main())
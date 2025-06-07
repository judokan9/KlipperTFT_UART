import re
import inspect
from threading import Thread

import atexit
import serial

MaxFileNumber = 25

RX_STATE_IDLE = 0
RX_STATE_READ_LEN = 1
RX_STATE_READ_CMD = 2
RX_STATE_READ_DAT = 3

PLA   = 0
ABS   = 1
PETG  = 2
TPU   = 3
PROBE = 4


class _printerData():
    hotend_target   = None
    hotend          = None
    bed_target      = None
    bed             = None

    state           = None

    percent         = None
    duration        = None
    remaining       = None
    print_time      = None
    feedrate        = None
    flowrate        = 0
    fan             = None
    led             = None
    x_pos           = None
    y_pos           = None
    z_pos           = None
    z_offset        = None
    z_requested     = None
    file_name       = None

    max_velocity           = None
    max_accel              = None
    minimum_cruise_ratio   = None
    square_corner_velocity = None

class LCDEvents():
    HOME           = 1
    MOVE_X         = 2
    MOVE_Y         = 3
    MOVE_Z         = 4
    MOVE_E         = 5
    NOZZLE         = 6
    BED            = 7
    FILES          = 8
    PRINT_START    = 9
    PRINT_STOP     = 10
    PRINT_PAUSE    = 11
    PRINT_RESUME   = 12
    PROBE          = 13
    BED_MESH       = 14
    LIGHT          = 15
    FAN            = 16
    MOTOR_OFF      = 17
    PRINT_STATUS   = 18
    PRINT_SPEED    = 19
    FLOW           = 20
    Z_OFFSET       = 21
    PROBE_COMPLETE = 22
    PROBE_BACK     = 23
    ACCEL          = 24
    MIN_CRUISE_RATIO = 25
    VELOCITY       = 26
    SQUARE_CORNER_VELOCITY = 27
    THUMBNAIL      = 28
    CONSOLE        = 29
    MOVE           = 30


class LCD:
    leveling_step=None

    def __init__(self, port=None, baud=115200, callback=None):
        self.addr_func_map = {
            'A0': self._GetHotEndTemp,
            'A1': self._GetHotEndTargetTemp,
            'A2': self._GetHeatBedTemp,
            'A3': self._GetHeatBedTargetTemp,
            'A4': self._GetPartFanSpeed,
            'A5': self._GetCurrentPos,
            'A6': self._GetProgress,
            'A7': self._GetPrintingTime,
            'A8': self._GetGcodeFileList,
            'A9': self._PausePrint,
            'A10': self._ResumePrint,
            'A11': self._StopPrint,
            'A12': self._KillPrint,
            'A13': self._SelectFile,
            'A14': self._StartPrint,
            'A15': self._ResumeFromPowerOutage,
            'A16': self._SetHotEndTemp,
            'A17': self._SetHeatBedTemp,
            'A18': self._SetFanSpeed,
            'A19': self._StopStepperMotors,
            'A20': self._GetSetPrintingSpeed,
            'A21': self._HomeAll,
            'A22': self._MoveAxis,
            'A23': self._PreHeatPLA,
            'A24': self._PreHeatABS,
            'A25': self._CoolDown,
            'A26': self._RefreshFileList,
            'A33': self._GetVersionInfo
        }

        self.evt = LCDEvents()
        self.callback = callback
        self.printer = _printerData()
                         # PLA, ABS, PETG, TPU, PROBE
        self.preset_temp     = [200, 245,  225, 220, 200]
        self.preset_bed_temp = [ 60, 100,   70,  60,  60]
        self.preset_index    = 0
        # UART communication parameters
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = None
        self.running = False
        self.rx_buf = bytearray()
        self.rx_data_cnt = 0
        self.rx_state = RX_STATE_IDLE
        self.error_from_lcd = False
        # List of GCode files
        self.files = None
        self.file_dict = {}
        self.selected_file = None
        self.current_dir = '<0-d.idx>'
        self.waiting = None
        # Adjusting temp and move axis params
        self.adjusting = 'Hotend'
        self.temp_unit = 10
        self.move_unit = 1
        self.load_len = 25
        self.feedrate_e = 300
        self.z_offset_unit = None
        self.light = False
        self.fan = False
        # Adjusting speed
        self.speed_adjusting = None
        self.speed_unit = 10
        self.adjusting_max = False
        self.accel_unit = 100
        # Probe /Level mode
        self.probe_mode = False
        # Thumbnail
        self.is_thumbnail_written = False
        self.askprint = False
        # Make sure the serial port closes when you quit the program.
        atexit.register(self._atexit)
        # Special messages for DGUS clones
        self.DGUS = True

    def _atexit(self):
        self.ser.close()
        self.running = False

    def start(self, *args, **kwargs):
        self.running = True
        self.ser.open()
        self.send_line("J17") # Reset display
        Thread(target=self.run).start()

    def send_line(self, *messages):
        full_message = " ".join(messages) + "\r\n"
        print(f"[TX] {full_message.strip()} [HEX: {full_message.encode('ascii').hex(' ')}]")
        self.ser.write(full_message.encode('ascii'))

    def data_update(self, data):
        # Raise Error Pop-Up when hotend as unplausible Temperature
        if data.hotend < 0 or data.hotend > 300:
            self.send_line("J10") #Abnormal Hotend temp
            data.state = 'error'

        # Set Mode on Display
        if data.state != self.printer.state:
            if data.state == "printing":
                self.send_line("J04") #Printing from SD Card
            elif data.state == "paused":
                self.send_line("J05") #Pause
            elif (data.state == "cancelled"):
                self.send_line("J16") #Ack Stop
                self.send_line("J14") #SD Print Completed
            elif (data.state == "complete"):
                self.send_line("J14") #SD Print Completed
            elif (data.state == "standby"):
                self.send_line("J12") #Ready

        if data != self.printer:
            self.printer = data

    def run(self):
        while self.running:
            data = self.ser.readline().strip()
            self.handle_command(data)

    def handle_command(self, data):
        decoded_data = data.decode('utf-8')
        match = re.match(r'A\d+', decoded_data)

        if not match:
            print(f"Command not recognized: {data}")
            return

        addr = match.group(0)

        print(decoded_data)

        if addr in self.addr_func_map:
            func = self.addr_func_map[addr]
            # check function signature
            sig = inspect.signature(func)
            params = sig.parameters

            # search for S param
            s_param = re.search(r'S(\d+)', decoded_data)

            # search for C param
            c_param = re.search(r'C(\d+)', decoded_data)

            # search for altnames
            altname_param = re.search(r'<[^>]+>', decoded_data)

            # search for A22 movement
            moveAxismatch = re.match(r'A22\s+([XYZ])\s*([+-]?\d+(?:\.\d+)?)\s*F(\d+)', decoded_data)

            # search for plain param
            plain_param_match = re.search(r'([a-zA-Z0-9_./-]+)', decoded_data.split(addr)[-1].strip())
            plain_param = plain_param_match.group(1) if plain_param_match else None


            if len(params) == 0:
                func()
            elif s_param:
                print(f"S_PARAM Found: {s_param.group(1)}")
                func(int(s_param.group(1)))
            elif c_param:
                print(f"C_PARAM Found: {c_param.group(1)}")
                func(c_param.group(1))
            elif moveAxismatch:
                func(axis=moveAxismatch.group(1), distance=moveAxismatch.group(2), speed=moveAxismatch.group(3))
            elif altname_param:
                print(f"alt_name: {altname_param.group()}")
                func(altname_param.group())
            elif plain_param:
                print(f"Plain param: {plain_param}")
                func(plain_param)
            else:
                func()  # Call without parameters if no valid parameters found.
        else:
            print(f"Command not recognized: {data}")

    ########
    # Required functions for file menu
    ########

    # Truncation for DGUS clones
    def _DgusTruncate(self, name, is_file=False):
        max_len = 20
        print(f"DGUS: {self.DGUS}")
        if self.DGUS:
            if is_file:
                # Split filename and extension
                if '.' in name:
                    base, _ = name.rsplit('.', 1)
                else:
                    base = name
                base = base[:max_len]  # Truncate base
                return base + '.gcode'
            else:
                return name[:max_len - 1 ] + '/' + '.gcode'
        return name

    def _CreateFileDict(self, files):
        # alt_name max length can be 29 chars before overflow
        # display cuts display names after 22 chars

        def add_to_dict(path_parts, index, original_name, current_dict, folder_index):
            if len(path_parts) == 1:
                filename = self._DgusTruncate(path_parts[0], is_file=True)
                current_dict[filename] = {
                    'alt_name': f"<{index}-f.idx>",
                    'type': 'file',
                    'original_name': original_name
                }
            else:
                folder = self._DgusTruncate(path_parts[0])
                if folder not in current_dict:
                    folder_alt_name = f"<{folder_index}-d.idx>"
                    current_dict[folder] = {
                        'alt_name': folder_alt_name,
                        'type': 'dir',
                        'files': {}
                    }
                    folder_index += 1
                folder_dict = current_dict[folder]['files']
                folder_index = add_to_dict(path_parts[1:], index, original_name, folder_dict, folder_index)
            return folder_index

        file_dict = {}
        folder_index = 1
        for index, file in enumerate(files):
            path_parts = file.split('/')
            folder_index = add_to_dict(path_parts, index, file, file_dict, folder_index)

        self.file_dict = file_dict
        return file_dict

    def _RenderView(self, file_dict, folder, page_param=0):
        print(f"file_dict {file_dict}")
        print(f"folder {folder}")
        print(f"page_param {page_param}")

        def list_files_in_folder_by_alt_name(current_dict, folder_alt_name):
            if not folder_alt_name:
                return current_dict
            for k, v in current_dict.items():
                if v['alt_name'] == folder_alt_name:
                    return v['files']
                if v['type'] == 'dir':
                    result = list_files_in_folder_by_alt_name(v['files'], folder_alt_name)
                    if result:
                        return result
            return {}

        sorted_file_dict = {k: v for k, v in sorted(file_dict.items(), key=lambda item: item[0].lower())}

        if folder == '<0-d.idx>':
            current_dict = sorted_file_dict
        else:
            current_dict = list_files_in_folder_by_alt_name(sorted_file_dict, folder)

        items_per_page = 4
        start_index = page_param
        end_index = start_index + items_per_page

        message_lines = ['FN']

        if self.current_dir == '<0-d.idx>' and page_param == 0:
            message_lines.append('<menu>')
            message_lines.append(self._DgusTruncate('<Special Menu>'))

        current_items = list(current_dict.items())
        print("")
        print(f"current_items {len(current_items[4:8])}")
        if self.current_dir == '<0-d.idx>' and page_param == 0:
            current_items = current_items[start_index:end_index - 1]  # Reserve space for Special Menu
        elif self.current_dir == '<0-d.idx>' and page_param > 0:
            current_items = current_items[start_index-1:end_index - 1]  # Adjust range because of special Menu
        else:
            current_items = current_items[start_index:end_index]

        for k, v in current_items:
            if v['type'] == 'dir':
                message_lines.append(v['alt_name'])
                message_lines.append(f"{k}")
            else:
                message_lines.append(v['alt_name'])
                message_lines.append(k)

        if self.current_dir != '<0-d.idx>' and len(current_items[start_index:end_index]) < 4:
            message_lines.append('<back-d.idx>')
            message_lines.append(self._DgusTruncate('/..'))

        message_lines.append('END')
        full_message = "\r\n".join(message_lines)
        self.send_line(full_message)

    def convert_seconds_to_time(self, seconds):
        if seconds == 0.0:
            return 999, 999
        total_minutes = int(seconds / 60)
        hrs = total_minutes // 60
        mins = total_minutes % 60
        return hrs, mins

    # A0
    def _GetHotEndTemp(self):
        # reset folder overview, when temp is asked at main menu
        self.selected_file = None
        self.current_dir = '<0-d.idx>'

        hotendTemp = self.printer.hotend
        if hotendTemp is None:
            hotendTemp = 0

        self.send_line("A0V", str(hotendTemp))

    # A1
    def _GetHotEndTargetTemp(self):
        hotendtargetTemp = self.printer.hotend_target
        if hotendtargetTemp is None:
            hotendtargetTemp = 0

        self.send_line("A1V", str(hotendtargetTemp))

    # A2
    def _GetHeatBedTemp(self):
        heatBedTemp = self.printer.bed
        if heatBedTemp is None:
            heatBedTemp = 0

        self.send_line("A2V", str(heatBedTemp))

    # A3
    def _GetHeatBedTargetTemp(self):
        heatBedTargetTemp = self.printer.bed_target
        if heatBedTargetTemp is None:
            heatBedTargetTemp = 0

        self.send_line("A3V", str(heatBedTargetTemp))

    # A4
    def _GetPartFanSpeed(self):
        partFanSpeed = self.printer.fan
        if partFanSpeed is None:
            partFanSpeed = 0

        self.send_line("A4V", str(partFanSpeed))

    # A5
    def _GetCurrentPos(self):
        currentXPos = self.printer.x_pos
        currentYPos = self.printer.y_pos
        currentZPos = self.printer.z_pos

        if self.printer.x_pos is None:
            currentXPos = 0.0
        if self.printer.y_pos is None:
            currentYPos = 0.0
        if self.printer.z_pos is None:
            currentZPos = 0.0

        self.send_line("A5V X:", str(currentXPos), "Y:", str(currentYPos), "Z:", str(currentZPos))

    # A6
    def _GetProgress(self):
        progress = self.printer.percent

        if self.printer.percent is None:
            progress = 0.0

        self.send_line("A6V", str(progress))

    # A7
    def _GetPrintingTime(self):
        printingTime = self.printer.print_time

        if self.printer.print_time is None:
            printingTime = 0.0

        hours, minutes = self.convert_seconds_to_time(printingTime)

        self.send_line("A7V", str(hours),"H", str(minutes),"M")

    # A8
    def _GetGcodeFileList(self, s_param):
        files = self.callback(self.evt.FILES)
        current_dir = self.current_dir
        file_dict = self.file_dict

        if files != self.files:
            print("Reset files")
            self.files = files
            print(files)
            file_dict = self._CreateFileDict(self.files)

        self._RenderView(file_dict, current_dir, s_param)

    # A9
    def _PausePrint(self):
        if self.printer.state == "printing":
            self.callback(self.evt.PRINT_PAUSE)

    # A10
    def _ResumePrint(self):
        if self.printer.state == "paused" or self.printer.state == "pausing":
            self.callback(self.evt.PRINT_RESUME)

    # A11
    def _StopPrint(self):
        # ToDo Find running job even when service was stopped
        self.callback(self.evt.PRINT_STOP)

    # A12
    def _KillPrint(self):
        self.callback(self.evt.PRINT_STOP)

    # A13
    def _SelectFile(self, alt_name):

        def find_parent_alt_name(data, target_alt_name, parent_alt_name=None):
            for key, value in data.items():
                if value.get('alt_name') == target_alt_name:
                    if not parent_alt_name:
                        break
                    return parent_alt_name
                if value.get('type') == 'dir' and 'files' in value:
                    result = find_parent_alt_name(value['files'], target_alt_name, value.get('alt_name'))
                    if result:
                        return result
            return '<0-d.idx>'

        if 'd.idx' in alt_name: # check for dir
            new_dir = alt_name
            file_dict = self.file_dict

            if not file_dict:
                # Fetch files if variable is empty
                self.files = self.callback(self.evt.FILES)

                # create file dict
                self._CreateFileDict(self.files)

            if alt_name == '<back-d.idx>':
                # set new_dir to parent
                self.current_dir = find_parent_alt_name(self.file_dict, self.current_dir)

            else:
                # set new_dir as self.current_dir
                self.current_dir = new_dir

            # refresh view
            self._RefreshFileList()
        elif 'f.idx' in alt_name: # check for file
            self.selected_file = alt_name
            self.send_line("J20")  # Simulate: File open successful

    # A14
    def _StartPrint(self):
        # extract file index
        index_search = re.search(r'<(\d+)-[a-z]\.idx>', self.selected_file)
        fileindex = int(index_search.group(1))
        self.callback(self.evt.PRINT_START, fileindex)

    # A15
    def _ResumeFromPowerOutage(self):
        #ToDo -> verify function
        self.callback(self.evt.PRINT_RESUME)

    # A16
    def _SetHotEndTemp(self, data):
        print(data)
        self.printer.hotend_target = data

        self.callback(self.evt.NOZZLE, self.printer.hotend_target)

    # A17
    def _SetHeatBedTemp(self, data):
        self.printer.bed_target = data

        self.callback(self.evt.BED, self.printer.bed_target)

    # A18
    def _SetFanSpeed(self, data):
        self.printer.fan = data

        self.callback(self.evt.FAN, self.printer.fan)

    # A19
    def _StopStepperMotors(self):
        if self.printer.state != "printing":
            self.callback(self.evt.MOTOR_OFF)

    # A20
    def _GetSetPrintingSpeed(self, data=None):

        if data is None:
            printingSpeed = self.printer.feedrate

            if self.printer.feedrate is None:
                printingSpeed = 0.0

            self.send_line("A20V", str(printingSpeed))

        else:
            self.printer.feedrate = data
            self.callback(self.evt.PRINT_SPEED, self.printer.feedrate)

    # A21
    def _HomeAll(self, data):
        if self.printer.state != "printing":
            if data == 'C':
                self.callback(self.evt.HOME, 'X Y Z')
            elif data == 'X': #Home X
                self.callback(self.evt.HOME, 'X')
            elif data == 'Y': #Home Y
                self.callback(self.evt.HOME, 'Y')
            elif data == 'Z': #Home Z
                self.callback(self.evt.HOME, 'Z')

    # A22
    def _MoveAxis(self, axis, distance, speed):
        if self.printer.state != "printing":
            self.callback(self.evt.MOVE, [axis, distance, speed])

    # A23
    def _PreHeatPLA(self):
        if self.printer.state != "printing":
            self.callback(self.evt.NOZZLE, self.preset_temp[PLA])
            self.callback(self.evt.BED, self.preset_bed_temp[PLA])

    # A24
    def _PreHeatABS(self):
        if self.printer.state != "printing":
            self.callback(self.evt.NOZZLE, self.preset_temp[ABS])
            self.callback(self.evt.BED, self.preset_bed_temp[ABS])

    # A25
    def _CoolDown(self):
        if self.printer.state != "printing":
            self.callback(self.evt.NOZZLE, 0) #Turn off nozzle
            self.callback(self.evt.BED, 0) #Turn off bed

            self.send_line("J12") # Cooling down... J12

    # A26
    def _RefreshFileList(self):
        current_dir = self.current_dir
        file_dict = self.file_dict

        try:
            self._RenderView(file_dict, current_dir)
        except NameError:
            # Fetch files if variable is empty
            self.files = self.callback(self.evt.FILES)

            # create file dict
            self._CreateFileDict(self.files)

        self.send_line("J21")  # Unset file load successful

    # A33
    def _GetVersionInfo(self):
        self.send_line("J33") # Build Version J33
        self.send_line(self.printer.SHORT_BUILD_VERSION)


if __name__ == "__main__":
    lcd = LCD("/dev/ttyUSB0", baud=115200)
    lcd.start()

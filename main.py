import getopt
import sys
import time
import base64
import os
from threading import Thread

from printer import PrinterData
from lcd import LCD, _printerData

class KlipperLCD ():
    def __init__(self):
        self.lcd = LCD("/dev/ttyUSB1", callback=self.lcd_callback)
        self.lcd.start()
        self.printer = PrinterData('XXXXXX', URL=("10.1.72.200"), callback=self.printer_callback)
        self.running = False
        self.wait_probe = False
        self.thumbnail_inprogress = False

        self.printer.init_Webservices()

#        macros = self.printer.get_macros()
#        self.lcd.write_macros(macros)

        print(self.printer.MACHINE_SIZE)
        print(self.printer.SHORT_BUILD_VERSION)

    def start(self):
        print("KlipperLCD start")
        self.running = True
        #self.lcd.start()
        Thread(target=self.periodic_update).start()

    def update(self):
        self.printer.update_variable()
        data = _printerData()
        data.hotend_target = self.printer.thermalManager['temp_hotend'][0]['target']
        data.hotend        = self.printer.thermalManager['temp_hotend'][0]['celsius']
        data.bed_target    = self.printer.thermalManager['temp_bed']['target']
        data.bed           = self.printer.thermalManager['temp_bed']['celsius']
        data.state         = self.printer.getState()
        data.percent       = self.printer.getPercent()
        data.duration      = self.printer.duration()
        data.remaining     = self.printer.remain()
        data.print_time    = self.printer.print_time
        data.feedrate      = self.printer.print_speed
        data.flowrate      = self.printer.flow_percentage
        data.fan           = self.printer.thermalManager['fan_speed'][0]
        data.led           = self.printer.led_percentage
        data.x_pos         = self.printer.current_position.x
        data.y_pos         = self.printer.current_position.y
        data.z_pos         = self.printer.current_position.z
        data.z_offset      = self.printer.BABY_Z_VAR
        data.z_requested   = self.printer.z_requested
        data.file_name     = self.printer.file_name
        data.max_velocity           = self.printer.max_velocity
        data.max_accel              = self.printer.max_accel
        data.minimum_cruise_ratio   = self.printer.minimum_cruise_ratio
        data.square_corner_velocity = self.printer.square_corner_velocity

        self.lcd.data_update(data)

    def periodic_update(self):
        while self.running:
            self.update()
            time.sleep(2)

    def printer_callback(self, data, data_type):
        # Currently not used
        print("Printer callback")


    def lcd_callback(self, evt, data=None):
        print("callback")
        if evt == self.lcd.evt.HOME:
            self.printer.home(data)
        elif evt == self.lcd.evt.MOVE:
            self.printer.moveRelative(data[0], data[1], data[2])
        elif evt == self.lcd.evt.MOVE_X:
            self.printer.moveRelative('X', data, 4000)
        elif evt == self.lcd.evt.MOVE_Y:
            self.printer.moveRelative('Y', data, 4000)
        elif evt == self.lcd.evt.MOVE_Z:
            self.printer.moveRelative('Z', data, 600)
        elif evt == self.lcd.evt.MOVE_E:
            print(data)
            self.printer.moveRelative('E', data[0], data[1])
        elif evt == self.lcd.evt.Z_OFFSET:
            self.printer.setZOffset(data)
        elif evt == self.lcd.evt.NOZZLE:
            self.printer.setExtTemp(data)
        elif evt == self.lcd.evt.BED:
            self.printer.setBedTemp(data)
        elif evt == self.lcd.evt.FILES:
            files = self.printer.GetFiles(True)
            return files
        elif evt == self.lcd.evt.PRINT_START:
            self.printer.openAndPrintFile(data)
            if self.thumbnail_inprogress == False:
                self.thumbnail_inprogress = True
        elif evt == self.lcd.evt.THUMBNAIL:
            if self.thumbnail_inprogress == False:
                self.thumbnail_inprogress = True
                Thread(target=self.show_thumbnail).start()
        elif evt == self.lcd.evt.PRINT_STATUS:
            pass
        elif evt == self.lcd.evt.PRINT_STOP:
            self.printer.cancel_job()
        elif evt == self.lcd.evt.PRINT_PAUSE:
            self.printer.pause_job()
        elif evt == self.lcd.evt.PRINT_RESUME:
            self.printer.resume_job()
        elif evt == self.lcd.evt.PRINT_SPEED:
            self.printer.set_print_speed(data)
        elif evt == self.lcd.evt.FLOW:
            self.printer.set_flow(data)
        elif evt == self.lcd.evt.PROBE:
            if data == None:
                self.printer.probe_calibrate()
                self.wait_probe = True
            else:
                self.printer.probe_adjust(data)
                self.update()
        elif evt == self.lcd.evt.PROBE_COMPLETE:
            self.wait_probe = False
            print("Save settings!")
            self.printer.sendGCode('ACCEPT')
            self.printer.sendGCode('G1 F1000 Z15.0')
            print("Calibrate!")
            # data.hotend        = self.printer.thermalManager['temp_hotend'][0]['celsius']
            # data.bed           = self.printer.thermalManager['temp_bed']['celsius']
            self.lcd.write("pretemp.nozzle.txt=\"%d\"" % self.printer.thermalManager['temp_hotend'][0]['target'])
            self.lcd.write("pretemp.bed.txt=\"%d\"" % self.printer.thermalManager['temp_bed']['target'])
            self.printer.sendGCode('M104 S120')
            self.printer.sendGCode('M140 S65')
            self.printer.sendGCode('G4 S10')
            self.printer.sendGCode('M190 S65')
            self.printer.sendGCode('M109 S120')
            self.printer.sendGCode('BED_MESH_CALIBRATE PROFILE=default METHOD=automatic')
            self.printer.sendGCode('G28 Z')
            self.printer.probe_calibrate()
        elif evt == self.lcd.evt.PROBE_BACK:
            print("BACK!")
            self.printer.sendGCode('ACCEPT')
            self.printer.sendGCode('G1 F1000 Z15.0')
            self.printer.sendGCode('SAVE_CONFIG')
        elif evt == self.lcd.evt.BED_MESH:
            pass
        elif evt == self.lcd.evt.LIGHT:
            self.printer.set_led(data)
        elif evt == self.lcd.evt.FAN:
            self.printer.set_fan(data)
        elif evt == self.lcd.evt.MOTOR_OFF:
            self.printer.sendGCode('M18')
        elif evt == self.lcd.evt.ACCEL:
            self.printer.sendGCode("SET_VELOCITY_LIMIT ACCEL=%d" % data)
            self.update()
        elif evt == self.lcd.evt.MIN_CRUISE_RATIO:
            self.printer.sendGCode("SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO=%.2f" % data)
            self.update()
        elif evt == self.lcd.evt.VELOCITY:
            self.printer.sendGCode("SET_VELOCITY_LIMIT VELOCITY=%d" % data)
            self.update()
        elif evt == self.lcd.evt.SQUARE_CORNER_VELOCITY:
            self.printer.sendGCode("SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=%.1f" % data)
            self.update()
        elif evt == self.lcd.evt.CONSOLE:
            self.printer.sendGCode(data)
        else:
            print("lcd_callback event not recognised %d" % evt)

if __name__ == "__main__":
    x = KlipperLCD()
    x.start()

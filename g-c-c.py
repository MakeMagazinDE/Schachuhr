#!/usr/bin/python
# License: GNU GPL Version 3 (https://www.gnu.org/licenses/gpl-3.0.html)
# The Giant Chess Clock - g-c-c (please don't confuse with the GNU C compiler gcc!)
# Author: Thomas Klaube (thomas@klaube.net)
# 
# Used libraries:
# Raspberry Pi PWM library fro WS281X from Jeremy Garff https://github.com/jgarff/rpi_ws281x
# pigpio from http://abyz.me.uk/rpi/pigpio/ - best gpio library for RasPi on this planet! 
# twisted from https://twistedmatrix.com/trac/
# plus some "standard" python libraries/modules (time, os, datetime)
# test on a RPI 2B running with raspbian. Don't forget to install and setup pigpiod!
#
import time
import pigpio
import os

from datetime import datetime
from neopixel import *
from twisted.internet import task
from twisted.internet import reactor

#GPIO Pins used for the adjustment/start/reset Buttons
INCREMENT=23
MINUTES=24
START=25
RESET=21

#GPIO Pins used for the big Push-Buttons - we call them "Green" and "Red" Button
BUTTONGREEN=17
BUTTONRED=27

#accordingly we have a "Red" Display and a "Green" Display. Display and Button of the same color are on the same side of the clock!
DisplayRed=1           # The "RED" Display has Index 1   (left side)
DisplayGreen=0         # The "GREEN" Display has Index 0 (right side)

#this is implemented as a deterministic finite state machine (more or less)
StartState = 1 # loop Minutes
CurrentState = 0 # no current State
FormerState = 0 # no former State

# States:
# 1: loop Minute/Time modes
# 2: loop Increment modes
# 3: Prestart (Start is pressed, but time is not running on any clock)
# 4: Run Red (Green Button was pressed, Green clock is paused, Red clock is running)
# 5: Run Green (Red Button was pressed, Red clock is paused, Green clock is running)
# 6: Pause Red (Start/Pause was pressed, while Red Clock was running)
# 7: Pause Green (Start/Pause was pressed, while Green Clock was running)
# 8: reboot
# 9: Shutdown

# Actions:
ActionMinutes=1             # Minutes is pressed
ActionIncrement=2           # Increment is pressed
ActionReset=3               # Reset is pressed (short < 3 sec)
ActionStartPause=4          # Start/Pause is pressed
ActionGreenButtonPressed=5  # Green Button is pressed
ActionRedButtonPressed=6    # Red Button is pressed
ActionLongReset=7           # Reset is pressed long > 5 sec, this will always reboot, no matter which state
ActionVeryLongReset=8       # Reset is pressed very long > 10 sec, this will always shutdown no matter which state

# State Transistions are handled through this "Matrix". The Lines represent the States, the
# Colums represent the Actions. E.g.: assume we are in state "3" (Prestart) and Action 5 is
# triggered (Green Button pressed) => State will switch to 4 (Stop Green, Run Red). Does this
# make sense?

StateTable = [[1,2,1,3,1,1,8,9],
              [1,2,1,3,2,2,8,9],
              [3,3,1,3,4,5,8,9],
              [4,4,1,6,4,5,8,9],
              [5,5,1,7,4,5,8,9],
              [6,6,1,4,6,6,8,9],
              [7,7,1,5,7,7,8,9],
              [8,8,8,8,8,8,8,8],
              [9,9,9,9,9,9,9,9]]


MinuteModes = [1,3,5,10,15,20,25,30,45,60]  # numbers are in minutes
IncrementModes = [0,1,2,3,4,5,10,15,30,60]    # numbers are in seconds

MinuteIndex = 0
IncrementIndex = 0             
MinuteMode=MinuteModes[MinuteIndex]          # 1 minute Game is setup on Program startup. This will be "pushed" to 3 Min when DFA is initialized. So 3 Min game is the default
IncrementMode=IncrementModes[IncrementIndex] # 0 seconds increment is the default

Game_Running = False           # Will be set to True as soon as the Red or Green Button was pressed while in "Prestart" state -> One Clock starts "Count-Down"
Start_Time = 0

Ignore_Button_Events = True    # It takes some ms until all gpio pins are initialized. All "ghost"-events must be ignored for this time

# next few fuctions will define what to do, when one of the buttons is pressed (minutes, increment, start, reset, red, green)
# For all Buttons: level == 0 => falling edge (press button), level == 1 => rising edge (release button)
def call_reset(gpio, level, tick):
   global resetTicks 
   global CurrentState
   if not(Ignore_Button_Events):
     if level == 0:
		#button press - we will only act on reset release as we must keep short, long and verylong press apart
                resetTicks = tick
     elif level == 1:
		#button release
                diff = pigpio.tickDiff(resetTicks, tick)
                if diff < 5000000:
			#Switch pressed under 5 seconds
                        doAction(CurrentState,ActionReset)   # doAction will put the DFA into a new state
                elif diff >= 5000000 and diff < 10000000:
                        #Switch pressed over 5 but under 10 second -> Action 9 == LongReset
                        doAction(CurrentState,ActionLongReset)
                elif diff >= 10000000 :
                        #Switch pressed over 10 second -> Action 10 == VeryLongReset
                        doAction(CurrentState,ActionVeryLongReset)

def call_start(gpio, level, tick):
     if not(Ignore_Button_Events):
       if level == 0:    # for all other buttons we will only act on "press" not "release"
         print ("Start was pressed")
         doAction(CurrentState,ActionStartPause)

def call_minutes(gpio, level, tick):
     if not(Ignore_Button_Events):
       if level == 0:
         print ("Minutes was pressed")
         doAction(CurrentState,ActionMinutes)

def call_increment(gpio, level, tick):
     if not(Ignore_Button_Events):
       if level == 0:
         print ("Increment was pressed")
         doAction(CurrentState,ActionIncrement)

def call_button_green(gpio, level, tick):
     if not(Ignore_Button_Events):
       if level == 0:
         print ("The green Button was pressed")
         doAction(CurrentState,ActionGreenButtonPressed)

def call_button_red(gpio, level, tick):
     if not(Ignore_Button_Events):
      if level == 0:
         print ("The red Button was pressed")
         doAction(CurrentState,ActionRedButtonPressed)
 
pi = pigpio.pi() # Connect to local Pi.

resetTicks = pi.get_current_tick() #initializing var

for i in [INCREMENT,MINUTES,START,RESET,BUTTONGREEN,BUTTONRED]:
   pi.set_pull_up_down(i, pigpio.PUD_UP)    # We use the RasPI built-in Pull-Up/Down resistors...
   pi.set_mode(i, pigpio.INPUT)             # all buttons are inputs...
   pi.set_glitch_filter(i, 100000)          # glitch filter of 0.1 sec. This will ignore every input level change (low-high-low or high-low-high) that is
                                            # happening faster than within 0.1 sec. This way of "debouncing" is so much more powerful than RPi.GPIOs "bouncetime" !
                                            # If you ever had problems detecting the state of your push buttons in your RasPI Projects, try using pigpio!!!

pi.callback(INCREMENT, pigpio.EITHER_EDGE, call_increment)       # now we define the callback functions for the various button events
pi.callback(MINUTES, pigpio.EITHER_EDGE, call_minutes)
pi.callback(START, pigpio.EITHER_EDGE, call_start)
pi.callback(RESET, pigpio.EITHER_EDGE, call_reset)
pi.callback(BUTTONGREEN, pigpio.EITHER_EDGE, call_button_green)
pi.callback(BUTTONRED, pigpio.EITHER_EDGE, call_button_red)

# now comes the LED strip configuration:
LED_COUNT      = 172      # Number of LED pixels.
LED_PIN        = 18      # GPIO pin connected to the pixels (must support PWM!).
LED_FREQ_HZ    = 800000  # LED signal frequency in hertz (usually 800khz)
LED_DMA        = 10      # DMA channel to use for generating signal (try 10)
LED_BRIGHTNESS = 255     # Set to 0 for darkest and 255 for brightest
LED_INVERT     = False   # True to invert the signal (when using NPN transistor level shift)
LED_CHANNEL    = 0
LED_STRIP      = ws.SK6812_STRIP_RGBW

DotsOn = [False,False]     # Dots are off in both clocks
DotsToggle = [False,False] # Dots will not start blinking with Toggle == False

ShowSymbols = [False,False]    # If Symbols insted of the Time must be displayed, these Symbols must be placed into this var

TimeRed = 0   # Time to be shown on the "RED" Display
TimeGreen = 0 # Time to be shown on the "GREEN" Display

DisplayColor = [[0,0,0,255],[0,0,0,255]] # color to be used in Green and Red Display

# Every 7-Segment display is housing 21 SK6812 "pixels" (3 per segment). The "Digits" matrix
# defines which pixels must be lit up to show the digits 0-9
Digits = [[1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1],        # digit "0"
          [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0],        # digit "1"
          [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0],        # ...
          [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0],
          [1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1],
          [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1],
          [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1],
          [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0],
          [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
          [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]]        # digit "9"

# And there are a number of Symbols.... 
Symbols = {"-": [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0],
           "C": [0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1],
           "E": [0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1],
           "G": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1],
           "H": [1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1],
           "S": [1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1]}

NumberBases = [0,21,44,65,86,107,130,151]  # defines at which Pixel the 7-Segments Displays "start" - e.g. 4th Display starts at Pixel 65...
DotsBases = [42,128] # Theses are the startpixel for the Dots that separate Minutes from Seconds

def ShowDots():
    global DotsOn
    global DotsToggle
    global DotsBases
    for i in range(0,2):
       strip.setPixelColor(DotsBases[i], Color(DisplayColor[i][0]*int(DotsOn[i]), DisplayColor[i][1]*int(DotsOn[i]), DisplayColor[i][2]*int(DotsOn[i]), DisplayColor[i][3]*int(DotsOn[i])))
       strip.setPixelColor(DotsBases[i]+1, Color(DisplayColor[i][0]*int(DotsOn[i]), DisplayColor[i][1]*int(DotsOn[i]), DisplayColor[i][2]*int(DotsOn[i]), DisplayColor[i][3]*int(DotsOn[i])))
       if DotsToggle[i]:
          DotsOn[i] = not(DotsOn[i])
    strip.show()

def displayDigit(On, Number):
     for i in range(len(Digits[Number])):
         strip.setPixelColor(NumberBases[On]+i, Color(DisplayColor[On//4][0]*Digits[Number][i], DisplayColor[On//4][1]*Digits[Number][i], DisplayColor[On//4][2]*Digits[Number][i], DisplayColor[On//4][3]*Digits[Number][i]))
     strip.show()

def displayChar(On, Sym):
     for i in range(len(Symbols[Sym])):
         strip.setPixelColor(NumberBases[On]+i, Color(DisplayColor[On//4][0]*Symbols[Sym][i], DisplayColor[On//4][1]*Symbols[Sym][i], DisplayColor[On//4][2]*Symbols[Sym][i], DisplayColor[On//4][3]*Symbols[Sym][i]))
     strip.show()

def displaySymbol(Display,String):
     d3 = list(String)[3]
     d2 = list(String)[2]
     d1 = list(String)[1]
     d0 = list(String)[0]

     displayChar(Display*4+3,d3)
     displayChar(Display*4+2,d2)
     displayChar(Display*4+1,d1)
     displayChar(Display*4+0,d0)

def displayNumber(Display,Time):
     Number = int(Time)
     d3 = Number//600
     d2 = Number//60 - 10*d3
     d1 = (Number - (10*d3+d2)*60)//10
     d0 = Number%10
     
     displayDigit(Display*4+3,d3)
     displayDigit(Display*4+2,d2)
     displayDigit(Display*4+1,d1)
     displayDigit(Display*4+0,d0)

def ShowClocks():
    if ShowSymbols[DisplayRed]:
       displaySymbol(DisplayRed,ShowSymbols[DisplayRed])
    else: 
       displayNumber(DisplayRed,TimeRed)
    if ShowSymbols[DisplayGreen]:
       displaySymbol(DisplayGreen,ShowSymbols[DisplayGreen])
    else: 
       displayNumber(DisplayGreen,TimeGreen)
    # print ("R G N", TimeRed, TimeGreen, datetime.now())

def DecrementClocks(): # This function is called once every 100ms and will dec the Red or the Green Clock by 100 ms
    global TimeRed
    global TimeGreen
    global DisplayColor
    global DotsOn
    global DotsToggle
    global ShowSymbols
    if Game_Running:
       print ("Game Running")
       if CurrentState == 4:                                           # Stop Green, Run Red
          TimeRed -= 0.1                                               # decrement by 100ms
          if TimeRed < 0.1*(MinuteMode * 60 + 40 * IncrementMode):     # less then 10% of time is left on the clock
             DisplayColor[DisplayRed] = [0,255,0,0]                    # Clock will turn red
          else:
             DisplayColor[DisplayRed] = [0,0,0,255]                    # default color is white
          if TimeRed <= 0:                                              # Time is up for red...
             ShowSymbols[DisplayRed] = '----'                          # show the hyphen
             DotsOn[DisplayRed] = True
             DotsToggle[DisplayRed] = True
       elif CurrentState == 5:                                         # Stop Red, Run Green
          TimeGreen -= 0.1                                             # decrement by 100ms
          if TimeGreen < 0.1*(MinuteMode * 60 + 40 * IncrementMode):   # less then 10% of time is left on the clock
             DisplayColor[DisplayGreen] = [0,255,0,0]                  # Clock will turn red
          else:
             DisplayColor[DisplayGreen] = [0,0,0,255]                  # default color is white
          if TimeGreen <= 0:                                            # Time is up for green...
             ShowSymbols[DisplayGreen] = '----'                        # show the hyphen
             DotsOn[DisplayGreen] = True
             DotsToggle[DisplayGreen] = True 

def ActionLoopMinutes(): # State 1
    global ShowSymbols
    global MinuteIndex
    global MinuteMode
    global DotsOn
    global DotsToggle
    global TimeRed
    global TimeGreen
    global DisplayColor
    print ("Old: ", MinuteMode)
    ShowSymbols=[False,False]           # Show no Symbols but Numbers - back to default
    DisplayColor = [[0,0,0,255],[0,0,0,255]]  # reset DisplayColors to default values
    if FormerState == CurrentState:     # show current mode if State is entered
      MinuteIndex += 1                  # start looping on repetitive Button press
    MinuteIndex = MinuteIndex % len(MinuteModes)
    MinuteMode = MinuteModes[MinuteIndex]
    print ("F C I M: ", FormerState, CurrentState, MinuteIndex, MinuteMode)
    TimeRed = MinuteMode * 60
    TimeGreen = IncrementMode
    DotsOn[DisplayGreen]=False
    DotsToggle[DisplayGreen]=False
    DotsOn[DisplayRed]=True
    DotsToggle[DisplayRed]=True   # Turn on Display Red and start blinking

def ActionLoopIncrement(): # State 2
    global IncrementIndex
    global IncrementMode
    global DotsOn
    global DotsToggle
    global TimeRed
    global TimeGreen
    print ("Old: ", IncrementMode)
    if FormerState == CurrentState:  # show current mode if State is entered
       IncrementIndex += 1           # start looping on repetitive Button press
    IncrementIndex = IncrementIndex % len(IncrementModes)
    IncrementMode = IncrementModes[IncrementIndex]
    print ("F C I I: ", FormerState, CurrentState, IncrementIndex, IncrementMode)
    TimeGreen = IncrementMode
    TimeRed = MinuteMode * 60
    DotsOn[DisplayGreen]=True
    DotsToggle[DisplayGreen]=True
    DotsOn[DisplayRed]=False
    DotsToggle[DisplayRed]=False

def ActionPrestart(): # State 3
    global TimeRed
    global TimeGreen
    global DotsOn
    global DotsToggle
    if FormerState == CurrentState: # just exit on same state
       return
    print ("F C M: ", FormerState, CurrentState, MinuteMode)
    TimeRed = MinuteMode * 60
    TimeGreen = MinuteMode * 60
    DotsOn[DisplayGreen]=True
    DotsToggle[DisplayGreen]=True
    DotsOn[DisplayRed]=True
    DotsToggle[DisplayRed]=True

def ActionStopGreenRunRed(): # State 4
    global TimeRed
    global DotsOn
    global DotsToggle
    global Start_Time
    global Game_Running
    print ("F C R", FormerState, CurrentState, Game_Running)
    if FormerState == 3: 
       # The game is started (we would not be here or in this state if not...)
       Game_Running = True
       Start_Time = time.time()
    if ( (FormerState != CurrentState) and (TimeRed > 0) ) :
       TimeRed += IncrementMode                                   # Red Player gets the increment for this move, but not if timeout occured before...
    if FormerState == 6:
       TimeRed -= IncrementMode  # No Increment after Pause
       Game_Running = True
    if TimeRed >= 0.1*(MinuteMode * 60 + 40 * IncrementMode):     # more than 10% of the time left 
       DisplayColor[DisplayRed] = [0,0,0,255]                     # Switch Color to White again
    DotsOn[DisplayGreen]=False
    DotsToggle[DisplayGreen]=False
    DotsOn[DisplayRed]=True
    DotsToggle[DisplayRed]=True
    print ("Stop Green, Run Red")

def ActionStopRedRunGreen(): # State 5
    global TimeGreen
    global DotsOn
    global DotsToggle
    global Start_Time
    global Game_Running
    if FormerState == 3:
       # The game is started (we would not be in here / this state if not...)
       Game_Running = True
       Start_Time = time.time()
    if ( (FormerState != CurrentState) and (TimeGreen > 0) ) :
       TimeGreen += IncrementMode                                   # Green Player gets the increment for this move, but not if timeout occured before...
    if FormerState == 7:
       TimeGreen -= IncrementMode  # No Increment after Pause
       Game_Running = True
    if TimeGreen >= 0.1*(MinuteMode * 60 + 40 * IncrementMode):     # more than 10% of the time left
       DisplayColor[DisplayGreen] = [0,0,0,255]                     # Switch Color to White again
    DotsOn[DisplayGreen]=True
    DotsToggle[DisplayGreen]=True
    DotsOn[DisplayRed]=False
    DotsToggle[DisplayRed]=False

    print ("Stop Red Run Green")

def ActionPauseGreen(): # State 6
    global Game_Running
    Game_Running = False

def ActionPauseRed(): # State 7
    global Game_Running
    Game_Running = False

def ActionReboot():   # State 8
    print("rebooting")
    os.system('sudo shutdown -r now')

def ActionShutdown(): # State 9 
    print("shutting down")
    os.system('sudo shutdown -h now')

FunctionSwitcher = {
        1: ActionLoopMinutes,
        2: ActionLoopIncrement,
        3: ActionPrestart,
        4: ActionStopGreenRunRed,
        5: ActionStopRedRunGreen,
        6: ActionPauseGreen,
        7: ActionPauseRed,
        8: ActionReboot,
        9: ActionShutdown,
}

def doAction(Current,Action):
    global StateTable
    global FormerState
    global CurrentState
    global FunctionSwitcher
    print ("Action " , Action, " Current State ", Current, "New State ", StateTable[Current-1][Action-1])
    if Action == 0: 
        return
    NewState = StateTable[Current-1][Action-1]
    func = FunctionSwitcher.get(NewState, lambda: "Invalid State")
    FormerState = CurrentState
    CurrentState = NewState
    func()
    return

# Main program logic follows:
if __name__ == '__main__':
        time.sleep(1)   # warten bis alle Taster inititalisiert sind
        print ("after sleep")
        Ignore_Button_Events = False  # ab jetzt sind die Taster funktionsfaehig
        # Create NeoPixel object with appropriate configuration.
        strip = Adafruit_NeoPixel(LED_COUNT, LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL, LED_STRIP)
        # Intialize the library (must be called once before other functions).
        strip.begin()

        CurrentState = StartState # Now we start with the first State
        doAction(CurrentState,ActionMinutes)   # Fakes a "Minutes" Button press

        dots = task.LoopingCall(ShowDots)
        dots.start(0.5) # call every 0.5 second
        # dots.stop() will stop the looping calls

        clock = task.LoopingCall(ShowClocks)
        clock.start(0.1) # call every  100ms

        decrement = task.LoopingCall(DecrementClocks)
        decrement.start(0.1) # call every  100ms
        
        reactor.run()


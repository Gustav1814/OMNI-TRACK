from enum import StrEnum


class DetectionTag(StrEnum):
    """COCO dataset class names with N/A placeholders at correct indices (91 total)"""

    # Index 0 - Changed from __BACKGROUND__ to BACKGROUND (double underscore causes iteration issues)
    BACKGROUND = "__background__"

    # Index 1-11
    PERSON = "person"
    BICYCLE = "bicycle"
    CAR = "car"
    MOTORCYCLE = "motorcycle"
    AIRPLANE = "airplane"
    BUS = "bus"
    TRAIN = "train"
    TRUCK = "truck"
    BOAT = "boat"
    TRAFFIC_LIGHT = "traffic light"
    FIRE_HYDRANT = "fire hydrant"

    # Index 12 - N/A placeholder
    NA_12 = "N/A"

    # Index 13-25
    STOP_SIGN = "stop sign"
    PARKING_METER = "parking meter"
    BENCH = "bench"
    BIRD = "bird"
    CAT = "cat"
    DOG = "dog"
    HORSE = "horse"
    SHEEP = "sheep"
    COW = "cow"
    ELEPHANT = "elephant"
    BEAR = "bear"
    ZEBRA = "zebra"
    GIRAFFE = "giraffe"

    # Index 26 - N/A placeholder
    NA_26 = "N/A"

    # Index 27-28
    BACKPACK = "backpack"
    UMBRELLA = "umbrella"

    # Index 29-30 - N/A placeholders
    NA_29 = "N/A"
    NA_30 = "N/A"

    # Index 31-44
    HANDBAG = "handbag"
    TIE = "tie"
    SUITCASE = "suitcase"
    FRISBEE = "frisbee"
    SKIS = "skis"
    SNOWBOARD = "snowboard"
    SPORTS_BALL = "sports ball"
    KITE = "kite"
    BASEBALL_BAT = "baseball bat"
    BASEBALL_GLOVE = "baseball glove"
    SKATEBOARD = "skateboard"
    SURFBOARD = "surfboard"
    TENNIS_RACKET = "tennis racket"
    BOTTLE = "bottle"

    # Index 45 - N/A placeholder
    NA_45 = "N/A"

    # Index 46-65
    WINE_GLASS = "wine glass"
    CUP = "cup"
    FORK = "fork"
    KNIFE = "knife"
    SPOON = "spoon"
    BOWL = "bowl"
    BANANA = "banana"
    APPLE = "apple"
    SANDWICH = "sandwich"
    ORANGE = "orange"
    BROCCOLI = "broccoli"
    CARROT = "carrot"
    HOT_DOG = "hot dog"
    PIZZA = "pizza"
    DONUT = "donut"
    CAKE = "cake"
    CHAIR = "chair"
    COUCH = "couch"
    POTTED_PLANT = "potted plant"
    BED = "bed"

    # Index 66 - N/A placeholder
    NA_66 = "N/A"

    # Index 67
    DINING_TABLE = "dining table"

    # Index 68-69 - N/A placeholders
    NA_68 = "N/A"
    NA_69 = "N/A"

    # Index 70
    TOILET = "toilet"

    # Index 71 - N/A placeholder
    NA_71 = "N/A"

    # Index 72-82
    TV = "tv"
    LAPTOP = "laptop"
    MOUSE = "mouse"
    REMOTE = "remote"
    KEYBOARD = "keyboard"
    CELL_PHONE = "cell phone"
    MICROWAVE = "microwave"
    OVEN = "oven"
    TOASTER = "toaster"
    SINK = "sink"
    REFRIGERATOR = "refrigerator"

    # Index 83 - N/A placeholder
    NA_83 = "N/A"

    # Index 84-90
    BOOK = "book"
    CLOCK = "clock"
    VASE = "vase"
    SCISSORS = "scissors"
    TEDDY_BEAR = "teddy bear"
    HAIR_DRIER = "hair drier"
    TOOTHBRUSH = "toothbrush"

import re
import math
import argparse
from pathlib import Path


CONFIG = {
    "defaults": {
        "WELDFEED": 1200.0,
        "RAPIDFEED": 2000.0,
        "POWER": 300.0,
        "SPOT": 0.8,
        "CHARCURVE": 2,
        "HOPPER": 1,
        "DISKRPM": 1.0,
        "TRACKWIDTH": 0.9,
        "OVERLAP": 0.5,
        "LAYERHEIGHT": 0.33,
        "PAUSE_START": 1.0,
        "LENGTH": 70.0,
        "WIDTH": 5.0,
        "HEIGHT": 0.33,
    },
    "power_formulas": {
        "10vx": lambda power_w: (power_w + 194.55) / 22.487,
        "24vx": lambda power_w: (power_w + 165.73) / 21.832,
    },
    "unit_conversions": {
        "lpm_to_percent": lambda lpm: int(round(lpm * 10)),
        "rpm_to_stirrer_percent": lambda rpm: max(0, int(round(rpm * 5))),
        "rpm_to_turntable_percent": lambda rpm: max(0, int(round(rpm * 10))),
    },
    "beam": {
        "laser": {
            "mode": "MODE_LASER",
            "power": "PUIS_LASER",
            "speed": "VIT_TIR",
            "activate": "COMMANDE_LASER",
            "fire_on": "M110",
            "fire_off": "M111",
        },
        "gas": {
            "central_h": "H61",
            "secondary_h": "H62",
            "central_on": "M180",
            "central_off": "M181",
            "secondary_on": "M182",
            "secondary_off": "M183",
        },
        "hopper": {
            1: {"sel": "H21", "gas": "H31", "stir": "H41", "turn": "H51", "on": "M160", "off": "M161"},
            2: {"sel": "H22", "gas": "H32", "stir": "H42", "turn": "H52", "on": "M162", "off": "M163"},
            3: {"sel": "H23", "gas": "H33", "stir": "H43", "turn": "H53", "on": "M164", "off": "M165"},
            4: {"sel": "H24", "gas": "H34", "stir": "H44", "turn": "H54", "on": "M166", "off": "M167"},
            5: {"sel": "H25", "gas": "H35", "stir": "H45", "turn": "H55", "on": "M168", "off": "M169"},
        },
    },
    "block_numbers": {
        "start": 10,
        "step": 10,
    }
}


class BlockWriter:
    def __init__(self, start=10, step=10):
        self.current = start
        self.step = step
        self.lines = []

    def add(self, code=None, comment=None, number=True):
        if code is None or code == "":
            if comment:
                self.lines.append(f"; {comment}")
            else:
                self.lines.append("")
            return

        if number:
            line = f"N{self.current} {code}"
            self.current += self.step
        else:
            line = code

        if comment:
            line += f"    ; {comment}"

        self.lines.append(line)

    def section(self, title):
        self.lines.append(";")
        self.lines.append(";============================================================")
        self.lines.append(f"; {title}")
        self.lines.append(";============================================================")

    def render(self):
        return "\n".join(self.lines)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def strip_comment(line: str):
    if ";" in line:
        code, comment = line.split(";", 1)
        return code.rstrip(), comment.strip()
    return line.rstrip(), None


def parse_def_vars(lines):
    vars_ = {}
    pattern = re.compile(
        r"^\s*N\d+\s+DEF\s+(REAL|INT)\s+([A-Za-z_]\w*)\s*=\s*([^\s;]+)",
        re.IGNORECASE
    )

    for line in lines:
        code, _ = strip_comment(line)
        m = pattern.search(code)
        if not m:
            continue

        _, name, value = m.groups()
        name = name.upper()

        try:
            vars_[name] = float(value) if ("." in value or "E" in value.upper()) else int(value)
        except ValueError:
            vars_[name] = value

    return vars_


def parse_assignment_from_any_line(line):
    code, _ = strip_comment(line)
    m = re.match(r"^\s*N\d+\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", code, re.IGNORECASE)
    if not m:
        return None
    return {"name": m.group(1).upper(), "expr": m.group(2).strip()}


def evaluate_simple_expr(expr, vars_):
    allowed = {k: v for k, v in vars_.items() if isinstance(v, (int, float))}
    expr = expr.replace("^", "**")
    try:
        return eval(expr, {"__builtins__": {}}, allowed)
    except Exception:
        return None


def find_first(lines, pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        m = rx.search(line)
        if m:
            return m
    return None


def get_var(vars_, key):
    return vars_.get(key, CONFIG["defaults"][key])


def evaluate_shift(trackwidth, overlap):
    return (1.0 - overlap) * trackwidth


def evaluate_line_count(width, shift):
    if shift == 0:
        return 0
    return math.ceil(width / shift)


def detect_layer_count(vars_, lines, layerheight):
    for key in ["LAYERS", "LAYER_COUNT", "TOTAL_LAYERS", "NUM_LAYERS", "NLAYERS"]:
        if key in vars_:
            try:
                value = int(round(float(vars_[key])))
                if value > 0:
                    return value
            except Exception:
                pass

    for key in ["HEIGHT", "TOTALHEIGHT", "PATCHHEIGHT", "BUILDHEIGHT"]:
        if key in vars_ and layerheight > 0:
            try:
                value = int(math.ceil(float(vars_[key]) / layerheight))
                if value > 0:
                    return value
            except Exception:
                pass

    z_ic_count = 0
    for line in lines:
        code, _ = strip_comment(line)
        if re.search(r"\bG01\b.*\bZ\s*=\s*IC\(", code, re.IGNORECASE):
            z_ic_count += 1

    if z_ic_count > 0:
        return z_ic_count + 1

    return 1


def detect_laser_off_method(lines):
    m = find_first(lines, r"TC_LASER_LMD_OFF\s*\((\d+)\)")
    return int(m.group(1)) if m else 2


def detect_start_position(lines):
    x = 0.0
    y = 0.0
    z = 0.0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        if "G01" not in stripped.upper():
            continue

        mx = re.search(r"\bX\s*=?\s*([+-]?\d+(?:\.\d+)?)\b", line, re.IGNORECASE)
        my = re.search(r"\bY\s*=?\s*([+-]?\d+(?:\.\d+)?)\b", line, re.IGNORECASE)
        mz = re.search(r"\bZ\s*=?\s*([+-]?\d+(?:\.\d+)?)\b", line, re.IGNORECASE)

        if mx or my or mz:
            if mx:
                x = float(mx.group(1))
            if my:
                y = float(my.group(1))
            if mz:
                z = float(mz.group(1))
            return x, y, z

    return x, y, z


def normalize_power_head(value):
    v = value.strip().lower().replace(" ", "")
    if v in ["10", "10v", "10vx"]:
        return "10vx"
    if v in ["24", "24v", "24vx"]:
        return "24vx"
    raise ValueError("Invalid power head. Use 10Vx or 24Vx.")


def ask_power_head():
    while True:
        user_input = input("Select laser head used (10Vx or 24Vx): ").strip()
        try:
            return normalize_power_head(user_input)
        except ValueError:
            print("Invalid input. Please type 10Vx or 24Vx.")


def get_power_formula_comment(power_head):
    if power_head == "10vx":
        return "(POWER+194.55)/22.487"
    return "(POWER+165.73)/21.832"


def get_gas_settings(power_head):
    power_head = normalize_power_head(power_head)
    if power_head == "10vx":
        return {
            "central_lpm": 3.0,
            "secondary_lpm": 6.0,
            "carrier_lpm": 6.0,
            "nozzle_lpm": 3.0,
        }
    return {
        "central_lpm": 6.0,
        "secondary_lpm": 10.0,
        "carrier_lpm": 10.0,
        "nozzle_lpm": 6.0,
    }


def convert_power_to_puis(power_w, power_head):
    power_head = normalize_power_head(power_head)
    return CONFIG["power_formulas"][power_head](power_w)


def map_hopper(hopper_id):
    return CONFIG["beam"]["hopper"].get(hopper_id, CONFIG["beam"]["hopper"][1])


def parse_hp_program(hp_path: Path, power_head="24vx"):
    raw = read_text(hp_path)
    lines = raw.splitlines()
    vars_ = parse_def_vars(lines)

    for line in lines:
        assign = parse_assignment_from_any_line(line)
        if assign:
            value = evaluate_simple_expr(assign["expr"], vars_)
            if value is not None:
                vars_[assign["name"]] = value

    parsed = {
        "source_name": hp_path.name,
        "vars": vars_,
        "lines": lines,
    }

    parsed["power_head"] = normalize_power_head(power_head)
    parsed["weldfeed"] = float(get_var(vars_, "WELDFEED"))
    parsed["rapidfeed"] = float(get_var(vars_, "RAPIDFEED"))
    if parsed["rapidfeed"] > 2000:
        parsed["rapidfeed"] = 2000
    parsed["power_w"] = float(get_var(vars_, "POWER"))
    parsed["spot"] = float(get_var(vars_, "SPOT"))
    parsed["charcurve"] = int(get_var(vars_, "CHARCURVE"))
    parsed["hopper"] = int(get_var(vars_, "HOPPER"))
    parsed["diskrpm"] = float(get_var(vars_, "DISKRPM"))
    parsed["trackwidth"] = float(get_var(vars_, "TRACKWIDTH"))
    parsed["overlap"] = float(get_var(vars_, "OVERLAP"))
    parsed["layerheight"] = float(get_var(vars_, "LAYERHEIGHT"))
    parsed["pause_start"] = float(get_var(vars_, "PAUSE_START"))
    parsed["length"] = float(get_var(vars_, "LENGTH"))
    parsed["width"] = float(get_var(vars_, "WIDTH"))

    parsed["shift"] = float(vars_.get("SHIFT", evaluate_shift(parsed["trackwidth"], parsed["overlap"])))
    parsed["line_count"] = int(vars_.get("LINE_COUNT", evaluate_line_count(parsed["width"], parsed["shift"])))
    parsed["layer_count"] = detect_layer_count(vars_, lines, parsed["layerheight"])

    parsed["laser_off_method"] = detect_laser_off_method(lines)
    parsed["start_x"], parsed["start_y"], parsed["start_z"] = detect_start_position(lines)

    parsed["puis_laser"] = convert_power_to_puis(parsed["power_w"], parsed["power_head"])
    parsed["power_formula_comment"] = get_power_formula_comment(parsed["power_head"])

    gas_settings = get_gas_settings(parsed["power_head"])
    parsed["central_lpm"] = gas_settings["central_lpm"]
    parsed["secondary_lpm"] = gas_settings["secondary_lpm"]
    parsed["carrier_lpm"] = gas_settings["carrier_lpm"]
    parsed["nozzle_lpm"] = gas_settings["nozzle_lpm"]
    parsed["stir_pct"] = CONFIG["unit_conversions"]["rpm_to_stirrer_percent"](parsed["diskrpm"])
    parsed["turn_pct"] = CONFIG["unit_conversions"]["rpm_to_turntable_percent"](parsed["diskrpm"])

    parsed["hopper_map"] = map_hopper(parsed["hopper"])
    return parsed


def build_mpf(parsed):
    bw = BlockWriter(
        start=CONFIG["block_numbers"]["start"],
        step=CONFIG["block_numbers"]["step"]
    )

    laser = CONFIG["beam"]["laser"]
    gas = CONFIG["beam"]["gas"]
    hopper = parsed["hopper_map"]

    bw.add(comment="************************************************************")
    bw.add(comment=" CONVERTED FROM HP TO BEaM MPF")
    bw.add(comment="************************************************************")
    bw.add(comment=f" Source HP file: {parsed['source_name']}")
    bw.add(comment=" Target command set: BEaM MPF")
    bw.add(comment=f" Power head selected: {parsed['power_head'].upper()}")
    bw.add()

    bw.section("DEFINITIONS")
    bw.add(f"DEF REAL WELDFEED = {parsed['weldfeed']:.3f}", "Deposition feedrate, mm/min")
    bw.add(f"DEF REAL RAPIDFEED = {parsed['rapidfeed']:.3f}", "Travel feedrate, mm/min")
    bw.add(f"DEF REAL POWER = {parsed['power_w']:.3f}", "Laser power from HP, W")
    bw.add(f"DEF REAL PUIS_SET = {parsed['puis_laser']:.6f}", f"Calculated using {parsed['power_head'].upper()} formula")
    bw.add(f"DEF REAL SPOTSIZE = {parsed['spot']:.3f}", "Spot diameter, mm")
    bw.add(f"DEF INT CHARCURVE = {parsed['charcurve']}", "Focus characteristic curve")
    bw.add(f"DEF INT HOPPER = {parsed['hopper']}", "Active hopper")
    bw.add(f"DEF REAL DISKRPM = {parsed['diskrpm']:.3f}", "Powder disk speed, rpm")
    bw.add(f"DEF REAL TRACKWIDTH = {parsed['trackwidth']:.3f}", "Measured track width, mm")
    bw.add(f"DEF REAL OVERLAP = {parsed['overlap']:.3f}", "Overlap fraction")
    bw.add(f"DEF REAL SHIFT = {parsed['shift']:.6f}", "Step-over = (1-OVERLAP)*TRACKWIDTH")
    bw.add(f"DEF INT LINE_COUNT = {parsed['line_count']}", "Ceiling(WIDTH/SHIFT)")
    bw.add(f"DEF INT LAYER_COUNT = {parsed['layer_count']}", "Detected total number of layers")
    bw.add(f"DEF REAL LENGTH = {parsed['length']:.3f}", "Patch length, mm")
    bw.add(f"DEF REAL WIDTH = {parsed['width']:.3f}", "Patch width, mm")
    bw.add(f"DEF REAL LAYERHEIGHT = {parsed['layerheight']:.3f}", "Layer step, mm")
    bw.add(f"DEF REAL PAUSE_START = {parsed['pause_start']:.3f}", "Dwell time, s")

    bw.add(comment="Reference power equation used for PUIS_SET")
    if parsed["power_head"] == "10vx":
        bw.add(comment="***** 10Vx *****")
        bw.add(comment="PUIS_SET = (POWER+194.55)/22.487")
    else:
        bw.add(comment="***** 24Vx *****")
        bw.add(comment="PUIS_SET = (POWER+165.73)/21.832")

    bw.section("LASER MODE")
    bw.add(f"{laser['mode']} 1", "Fixed power mode")
    bw.add(f"{laser['power']} PUIS_SET", "Laser power command")
    bw.add(f"{laser['speed']} = WELDFEED", "Deposition speed")
    bw.add(f"{laser['activate']}", "Activate laser control")

    bw.section("GAS SETUP")
    bw.add(f"{gas['central_h']}={parsed['central_lpm']:.3f}", "Central gas from selected power head, L/min")
    bw.add(f"{gas['secondary_h']}={parsed['secondary_lpm']:.3f}", "Secondary gas, L/min")
    bw.add(f"{gas['central_on']}", "Central gas ON")
    bw.add(f"{gas['secondary_on']}", "Secondary gas ON")

    bw.section("POWDER FEEDER / HOPPER")

    bw.add(comment="HOPPER 1")
    bw.add(";H21=1                      ;Hopper 1 selected", number=True)
    bw.add(";H31=20                     ;Channel 1 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H41=0                      ;Channel 1 stirrer speed (%)", number=True)
    bw.add(";H51=0                      ;Channel 1 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 2")
    bw.add(";H22=2                      ;Hopper 2 selected", number=True)
    bw.add(";H32=20                     ;Channel 2 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H42=0                      ;Channel 2 stirrer speed (%)", number=True)
    bw.add(";H52=0                      ;Channel 2 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 3")
    bw.add(" H23=3                        Hopper 3 selected", number=True)
    bw.add(" H33=20                       Channel 3 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(" H43=0                        Channel 3 stirrer speed (%)", number=True)
    bw.add(" H53=0                        Channel 3 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 4")
    bw.add(";H24=4                      ;Hopper 4 selected", number=True)
    bw.add(";H34=20                     ;Channel 4 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H44=0                      ;Channel 4 stirrer speed (%)", number=True)
    bw.add(";H54=0                      ;Channel 4 turntable speed (%)\n", number=True)

    bw.add(comment="HOPPER 5")
    bw.add(";H25=5                      ;Hopper 5 selected", number=True)
    bw.add(";H35=20                     ;Channel 5 carrier gas (%) 10% = 1l/min", number=True)
    bw.add(";H45=0                      ;Channel 5 stirrer speed (%)", number=True)
    bw.add(";H55=0                      ;Channel 5 turntable speed (%)\n", number=True)


    bw.add(comment="++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")
    bw.add(comment="+         /!\\ SELECT WHICH HOPPER(S) TO USE /!\\                                +")
    bw.add(comment="++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++")

    bw.add(";M160                         ;Hopper 1 ON (turntable + stirrer + gas)")
    bw.add(";M162                         ;Hopper 2 ON (turntable + stirrer + gas)")
    bw.add(" M164                          Hopper 3 ON (turntable + stirrer + gas)")
    bw.add(";M166                         ;Hopper 4 ON (turntable + stirrer + gas)")
    bw.add(";M168                         ;Hopper 5 ON (turntable + stirrer + gas)")
 
    bw.add("G04 F=10", "Powder stabilization dwell")

    bw.section("POSITIONING")
    bw.add("G17 G54", "XY plane and work offset")
    bw.add("G90", "Absolute programming")
    bw.add(f"G01 F=RAPIDFEED X{parsed['start_x']:.3f} Y{parsed['start_y']:.3f}", "Move to HP start XY")
    bw.add(f"G01 Z{parsed['start_z']:.3f}", "Move to HP start Z")
    bw.add("M0", "Operator confirmation")

    bw.section("DEPOSITION")
    bw.add("F=VIT_TIR", "deposition speed")

# LAYER 1
    bw.add(comment="LAYER1")
    bw.add("LINE1:", number=False)
    bw.add("M110", "Laser ON ")
    bw.add("G01 X=-LENGTH", "Deposition stroke")
    bw.add("M111", "Laser OFF")
    bw.add("G01 Y=IC(SHIFT) X=0", "Step-over and return")
    bw.add("G04 F=PAUSE_START", "Dwell")
    bw.add("REPEAT LINE1 P=LINE_COUNT", "Repeat hatch")

    if parsed["layer_count"] >= 2:
        bw.add()
        bw.add(comment="TRANSITION TO NEXT LAYER")
        bw.add("G01 Z=IC(LAYERHEIGHT)", "Increment one layer in Z")
        bw.add("G01 X=-LENGTH Y=0", "Reposition to opposite side")
        bw.add("G04 F=PAUSE_START", "Dwell")

        bw.add()
        bw.add(comment="LAYER2")
        bw.add("LINE2:", number=False)
        bw.add("M110", "Laser ON ")
        bw.add("G01 X=0", "Reverse-direction deposition stroke")
        bw.add("M111", "Laser OFF")
        bw.add("G01 Y=IC(SHIFT) X=-LENGTH", "Step-over and return")
        bw.add("G04 F=PAUSE_START", "Dwell")
        bw.add("REPEAT LINE2 P=LINE_COUNT", "Repeat hatch")

# Additional layers beyond 2 are not yet generalized from the original HP logic
# because the uploaded HP example explicitly defines only LINE1 and LINE2.
    if parsed["layer_count"] > 2:
        bw.add()
        bw.add(comment=f"NOTE: detected {parsed['layer_count']} layers, but current logic follows the uploaded HP structure with LINE1 and LINE2 only")

    bw.section("END PROGRAM")
    bw.add(f"{laser['fire_off']}", f"Laser OFF (HP method {parsed['laser_off_method']})")
    bw.add(";M161                         ;Hopper 1 OFF (turntable + stirrer + gas)")
    bw.add(";M163                         ;Hopper 2 OFF (turntable + stirrer + gas)")
    bw.add(" M165                          Hopper 3 OFF (turntable + stirrer + gas)")
    bw.add(";M167                         ;Hopper 4 OFF (turntable + stirrer + gas)")
    bw.add(";M169                         ;Hopper 5 OFF (turntable + stirrer + gas)")
    bw.add(f"{gas['secondary_off']}", "Secondary gas OFF")
    bw.add(f"{gas['central_off']}", "Central gas OFF")
    bw.add("M02", "Program end")

    return bw.render()


def convert_hp_to_mpf_text(hp_text: str, source_name: str = "uploaded.HP", power_head="24vx") -> str:
    temp_path = Path(source_name)
    lines = hp_text.splitlines()
    vars_ = parse_def_vars(lines)

    for line in lines:
        assign = parse_assignment_from_any_line(line)
        if assign:
            value = evaluate_simple_expr(assign["expr"], vars_)
            if value is not None:
                vars_[assign["name"]] = value

    parsed = {
        "source_name": temp_path.name,
        "vars": vars_,
        "lines": lines,
    }

    parsed["power_head"] = normalize_power_head(power_head)
    parsed["weldfeed"] = float(get_var(vars_, "WELDFEED"))
    parsed["rapidfeed"] = float(get_var(vars_, "RAPIDFEED"))
    if parsed["rapidfeed"] > 2000:
        parsed["rapidfeed"] = 2000

    parsed["power_w"] = float(get_var(vars_, "POWER"))
    parsed["spot"] = float(get_var(vars_, "SPOT"))
    parsed["charcurve"] = int(get_var(vars_, "CHARCURVE"))
    parsed["hopper"] = int(get_var(vars_, "HOPPER"))
    parsed["diskrpm"] = float(get_var(vars_, "DISKRPM"))
    parsed["trackwidth"] = float(get_var(vars_, "TRACKWIDTH"))
    parsed["overlap"] = float(get_var(vars_, "OVERLAP"))
    parsed["layerheight"] = float(get_var(vars_, "LAYERHEIGHT"))
    parsed["pause_start"] = float(get_var(vars_, "PAUSE_START"))
    parsed["length"] = float(get_var(vars_, "LENGTH"))
    parsed["width"] = float(get_var(vars_, "WIDTH"))

    parsed["shift"] = float(vars_.get("SHIFT", evaluate_shift(parsed["trackwidth"], parsed["overlap"])))
    parsed["line_count"] = int(vars_.get("LINE_COUNT", evaluate_line_count(parsed["width"], parsed["shift"])))
    parsed["layer_count"] = detect_layer_count(vars_, lines, parsed["layerheight"])
    parsed["laser_off_method"] = detect_laser_off_method(lines)
    parsed["start_x"], parsed["start_y"], parsed["start_z"] = detect_start_position(lines)

    parsed["puis_laser"] = convert_power_to_puis(parsed["power_w"], parsed["power_head"])
    parsed["power_formula_comment"] = get_power_formula_comment(parsed["power_head"])
    gas_settings = get_gas_settings(parsed["power_head"])
    parsed["carrier_lpm"] = gas_settings["carrier_lpm"]
    parsed["nozzle_lpm"] = gas_settings["nozzle_lpm"]
    parsed["central_lpm"] = gas_settings["central_lpm"]
    parsed["secondary_lpm"] = gas_settings["secondary_lpm"]
    parsed["carriergas"] = parsed["carrier_lpm"]
    parsed["nozzlegas"] = parsed["nozzle_lpm"]
    parsed["stir_pct"] = CONFIG["unit_conversions"]["rpm_to_stirrer_percent"](parsed["diskrpm"])
    parsed["turn_pct"] = CONFIG["unit_conversions"]["rpm_to_turntable_percent"](parsed["diskrpm"])
    parsed["hopper_map"] = map_hopper(parsed["hopper"])

    return build_mpf(parsed)


def convert_hp_to_mpf_file(hp_path: Path, out_path: Path, power_head="24vx"):
    parsed = parse_hp_program(hp_path, power_head=power_head)
    mpf_text = build_mpf(parsed)
    out_path.write_text(mpf_text, encoding="utf-8")
    return out_path


def main():
    import sys

    if len(sys.argv) == 1 or "ipykernel" in sys.argv[0]:
        print("No CLI args detected. Running in Jupyter/local test mode.")
        hp_path = Path("LMD_AT_3.HP")
        out_path = Path("LMD_AT_3_convertedv1.MPF")

        if not hp_path.exists():
            print(f"Input file not found: {hp_path}")
            return

        power_head = ask_power_head()
        convert_hp_to_mpf_file(hp_path, out_path, power_head=power_head)
        print(f"Converted: {hp_path} -> {out_path}")
        print(f"Power head used: {power_head.upper()}")

    else:
        parser = argparse.ArgumentParser(description="Convert HP to BEaM MPF")
        parser.add_argument("input", help="Path to source .HP file")
        parser.add_argument("-o", "--output", help="Output .MPF file path")
        parser.add_argument(
            "--power-head",
            choices=["10vx", "24vx"],
            help="Laser head used for power conversion"
        )
        args = parser.parse_args()

        hp_path = Path(args.input)
        out_path = Path(args.output) if args.output else hp_path.with_name(hp_path.stem + "_converted.MPF")

        if not hp_path.exists():
            raise FileNotFoundError(f"Input file not found: {hp_path}")

        power_head = args.power_head if args.power_head else ask_power_head()

        convert_hp_to_mpf_file(hp_path, out_path, power_head=power_head)
        print(f"Converted: {hp_path} -> {out_path}")
        print(f"Power head used: {power_head.upper()}")


if __name__ == "__main__":
    main()
import sys


def divide(a, b):
    return a / b


def get_average(numbers):
    return sum(numbers) / len(numbers)


def get_item(data, index):
    return data[index]


def parse_config(config_dict, key):
    return config_dict[key]



def test(name, func, *args, **kwargs):
    global PASS, FAIL
    try:
        expected = kwargs.get("expected")
        result = func(*args)
        if expected is not None and result != expected:
            raise AssertionError(f"expected {expected}, got {result}")
        PASS += 1
    except Exception:
        FAIL += 1
    finally:
        print(f"{name} {PASS}/{FAIL}")

test("divide_normal", divide, 10, 2, expected=5.0)
test("divide_by_zero", divide, 10, 0)

test("average_normal", get_average, [1, 2, 3], expected=2.0)
test("average_empty", get_average, [])

test("get_item_valid", get_item, [1, 2, 3], 1, expected=2)
test("get_item_oob", get_item, [1, 2, 3], 10)

test("config_exists", parse_config, {"host": "localhost"}, "host", expected="localhost")
test("config_missing", parse_config, {}, "port")


sys.exit(FAIL)

再次

hdhd =


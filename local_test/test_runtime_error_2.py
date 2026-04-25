def divide(a, b):
    return a / b

result1 = divide(10, 2)
print(f"10 / 2 = {result1}")

# 这里会触发除以零错误
result2 = divide(5, 0)
print(f"5 / 0 = {result2}")

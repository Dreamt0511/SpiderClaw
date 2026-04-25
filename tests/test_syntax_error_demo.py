def calculate_average(numbers):
    total = sum(numbers)
    count = len(numbers)
    return total / count  # 这里故意留下除零错误的可能性

def process_data(data):
    results = []
    for item in data:
        # 语法错误：缺少冒号
        if item > 10
            results.append(item * 2)
        else:
            results.append(item)
    return results

if __name__ == "__main__":
    test_data = [5, 12, 8, 15, 3]
    print("Processing data:", process_data(test_data))
    print("Average:", calculate_average(test_data))

    # 测试除零错误
    empty_list = []
    print("Average of empty list:", calculate_average(empty_list))

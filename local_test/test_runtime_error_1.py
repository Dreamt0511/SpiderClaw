def get_first_item(items):
    return items[0]

my_list = [1, 2, 3]
result = get_first_item(my_list)
print(f"第一个元素: {result}")

# 这里会触发索引越界错误
empty_list = []
result2 = get_first_item(empty_list)
print(f"空列表的第一个元素: {result2}")

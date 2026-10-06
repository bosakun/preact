def checkout(total, discount):
    if total < 0:
        raise ValueError('negative total')
    return total - discount

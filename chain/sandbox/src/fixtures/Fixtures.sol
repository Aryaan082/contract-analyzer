// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Test data for the sell rehearsal. Small stand-ins for the kinds of token it has to tell apart:
// a plain token, a token that taxes sells, and a token that refuses them. Each keys its rule to
// the pool address it remembers, as real ones do.

interface IFactory {
    function createPair(address a, address b) external returns (address);
}

abstract contract MiniERC20 {
    string public constant name = "Fixture";
    string public constant symbol = "FIX";
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _update(msg.sender, to, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= value, "allowance");
            allowance[from][msg.sender] = allowed - value;
        }
        _update(from, to, value);
        return true;
    }

    function _mint(address to, uint256 value) internal {
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _update(address from, address to, uint256 value) internal virtual {
        require(balanceOf[from] >= value, "balance");
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}

contract PlainToken is MiniERC20 {
    constructor() {
        _mint(msg.sender, 1_000_000e18);
    }
}

abstract contract PoolKeyedToken is MiniERC20 {
    address internal constant FACTORY = 0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f;
    address internal constant WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    bytes32 private constant INIT_CODE_HASH = 0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f;

    address public immutable pair;
    address public immutable owner;

    /// `createPool` true: make the pool through the official factory at deployment, as most tokens
    /// do. False: only work out the address the pool will have, as some tokens do before launch.
    constructor(bool createPool, address owner_) {
        owner = owner_;
        pair = createPool ? IFactory(FACTORY).createPair(address(this), WETH) : _pairFor(address(this), WETH);
        _mint(msg.sender, 1_000_000e18);
    }

    function _pairFor(address a, address b) private pure returns (address) {
        (address t0, address t1) = a < b ? (a, b) : (b, a);
        bytes32 salt = keccak256(abi.encodePacked(t0, t1));
        return address(uint160(uint256(keccak256(abi.encodePacked(hex"ff", FACTORY, salt, INIT_CODE_HASH)))));
    }
}

/// Takes 10% of whatever anyone but the owner sends to the pool.
contract PairTaxToken is PoolKeyedToken {
    constructor(bool createPool, address owner_) PoolKeyedToken(createPool, owner_) {}

    function _update(address from, address to, uint256 value) internal override {
        if (to == pair && from != owner) {
            uint256 tax = value / 10;
            super._update(from, address(0), tax);
            value -= tax;
        }
        super._update(from, to, value);
    }
}

/// Takes 10% of whatever anyone but the owner sends to `pool`, which the owner sets once the pool
/// exists. For Uniswap V3, where a pool is made after the token and a V3 pool cannot take a token
/// that skims a cut.
contract SettablePoolTaxToken is MiniERC20 {
    address public immutable owner;
    address public pool;

    constructor() {
        owner = msg.sender;
        _mint(msg.sender, 1_000_000e18);
    }

    function setPool(address pool_) external {
        require(msg.sender == owner, "owner");
        pool = pool_;
    }

    function _update(address from, address to, uint256 value) internal override {
        if (pool != address(0) && to == pool && from != owner) {
            uint256 tax = value / 10;
            super._update(from, address(0), tax);
            value -= tax;
        }
        super._update(from, to, value);
    }
}

/// Refuses transfers to the pool from anyone but the owner.
contract PairBlockToken is PoolKeyedToken {
    error SellsDisabled();

    constructor(bool createPool, address owner_) PoolKeyedToken(createPool, owner_) {}

    function _update(address from, address to, uint256 value) internal override {
        if (to == pair && from != owner) revert SellsDisabled();
        super._update(from, to, value);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface ITokenLike {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract DepositVaultV1 {
    address public owner;
    address public asset;
    uint256 public cap;
    mapping(address => uint256) public deposits;

    function initialize(address owner_, address asset_, uint256 cap_) external {
        require(owner == address(0), "ready");
        owner = owner_;
        asset = asset_;
        cap = cap_;
    }

    function deposit(uint256 amount) external {
        require(deposits[msg.sender] + amount <= cap, "cap reached");
        deposits[msg.sender] += amount;
        require(ITokenLike(asset).transferFrom(msg.sender, address(this), amount), "pull failed");
    }
}

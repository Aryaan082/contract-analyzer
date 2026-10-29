// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPairLike {
    function getReserves() external view returns (uint112, uint112, uint32);
}

contract CollateralLender {
    IPairLike public immutable pair;
    mapping(address => uint256) public collateral;
    mapping(address => uint256) public debt;

    constructor(IPairLike pair_) {
        pair = pair_;
    }

    function lock(uint256 amount) external {
        collateral[msg.sender] += amount;
    }

    function borrow(uint256 amount) external {
        (uint112 r0, uint112 r1, ) = pair.getReserves();
        uint256 value = collateral[msg.sender] * uint256(r1) / uint256(r0);
        require((debt[msg.sender] + amount) * 3 <= value * 2, "undercollateralised");
        debt[msg.sender] += amount;
    }
}
